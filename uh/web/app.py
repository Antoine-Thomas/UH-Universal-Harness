"""FastAPI application for ``uh web`` (Jalon 5).

Optional: importing this module requires the ``[web]`` extra (fastapi,
uvicorn). The core package never imports it.

Security model (see ``docs/WEB_DESIGN.md``):

* loopback bind by default, ``Host``/``Origin`` checked on every request;
* one random token per process, accepted as ``?token=`` once (then an
  ``HttpOnly; SameSite=Strict`` cookie) or as ``Authorization: Bearer``;
* every ``/api`` route answers 401 without a valid token;
* strict CSP, no inline script, no inline style, no CORS;
* the terminal toolset is disabled unless ``--allow-terminal``;
* no API key is ever serialised.
"""

from __future__ import annotations

import asyncio
import io
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, attachments
from .. import commands as uh_commands
from .. import config as uh_config
from ..console import ChatSession, Console
from ..providers import Provider, ProviderError
from ..store import Store, display_id
from ..toolsets import build_toolsets
from .sse import frame

STATIC_DIR = Path(__file__).parent / "static"
COOKIE_NAME = "uh_token"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
#: An import arrives as one body: the cap stops a wrong file from filling the
#: disk, and the route stops reading at the boundary instead of after it.
MAX_IMPORT_BYTES = 256 * 1024 * 1024


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _hostname(host_header: str) -> str:
    value = (host_header or "").strip()
    if value.startswith("["):  # [::1]:8765
        return value.split("]")[0] + "]"
    return value.split(":")[0]


def _allowed_hosts(host: str, port: int) -> set[str]:
    names = {host, "127.0.0.1", "localhost"}
    allowed: set[str] = set()
    for name in names:
        allowed.add(name)
        allowed.add(f"{name}:{port}")
    return allowed


def _allowed_origins(host: str, port: int) -> set[str]:
    origins = set()
    for name in {host, "127.0.0.1", "localhost"}:
        origins.add(f"http://{name}:{port}")
    return origins


@contextmanager
def _store(app: FastAPI) -> Iterator[Store]:
    store = Store(app.state.settings.store_path)
    try:
        yield store
    finally:
        store.close()


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-uh-token")


def require_token(request: Request) -> None:
    supplied = request.cookies.get(COOKIE_NAME) or _bearer(request)
    if not supplied or not secrets.compare_digest(supplied, request.app.state.token):
        raise HTTPException(status_code=401, detail="missing or invalid token")


def _session_or_404(app: FastAPI, session_id: str) -> dict:
    with _store(app) as store:
        session = store.get_session(session_id)
        if session is None:
            matches = [
                s for s in store.list_sessions(limit=10_000)
                if s["id"].startswith(session_id) or display_id(s["id"]) == session_id
            ]
            if len(matches) == 1:
                session = matches[0]
    if session is None:
        raise HTTPException(status_code=404, detail=f"no session {session_id}")
    return session


def _toolsets(app: FastAPI) -> dict:
    toolsets = build_toolsets(app.state.settings.workspace)
    if not app.state.settings.allow_terminal:
        toolsets.pop("terminal", None)
    return toolsets


def _provider_for(app: FastAPI, session_id: Optional[str] = None) -> tuple[str, str, Provider]:
    kind, model = app.state.session_models.get(session_id or "", (app.state.kind, app.state.model))
    if not kind:
        raise ProviderError(app.state.provider_error or "no provider configured")
    provider = app.state.provider_factory(kind, model)
    return kind, model, provider


# --------------------------------------------------------------------------- #
# application factory
# --------------------------------------------------------------------------- #
def create_app(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    token: Optional[str] = None,
    allow_terminal: bool = False,
    workspace: Optional[Path] = None,
    cfg: Optional[uh_config.Config] = None,
    provider_factory: Optional[Callable[[str, str], Provider]] = None,
    kind: Optional[str] = None,
    model: Optional[str] = None,
    static_dir: Optional[Path] = None,
) -> FastAPI:
    app = FastAPI(title="UH", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    cfg = cfg or uh_config.load()
    app.state.cfg = cfg
    app.state.token = token or secrets.token_urlsafe(32)
    app.state.active = {}  # dict[str, ChatSession]: in-flight turns
    app.state.session_models = {}  # dict[str, tuple[str, str]]: per-session model
    app.state.pulling = set()  # model names a background `ollama pull` is fetching
    app.state.settings = type(
        "Settings",
        (),
        {
            "host": host,
            "port": port,
            "allow_terminal": allow_terminal,
            "workspace": (workspace or Path.cwd()).resolve(),
            "store_path": cfg.resolved_store_path(),
        },
    )()

    if provider_factory is None:
        from ..cli import _make_provider as provider_factory  # lazy: avoids a cycle

    app.state.provider_factory = provider_factory
    app.state.provider_error = ""
    if kind:
        # Explicit initial provider (tests, embedding): no discovery, no network.
        app.state.kind = kind
        app.state.model = model or ""
    else:
        app.state.kind = ""
        app.state.model = ""
        try:
            from ..cli import _build_chain

            chain, resolved = _build_chain(
                None,
                str(cfg.get("default_model") or "") or None,
                str(cfg.get("omnirouter_model") or ""),
                preferred=str(cfg.get("default_provider") or "") or None,
            )
            app.state.kind = chain[0][0]
            app.state.model = resolved
        except ProviderError as exc:
            app.state.provider_error = str(exc)

    allowed_hosts = _allowed_hosts(host, port)
    allowed_origins = _allowed_origins(host, port)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "host not allowed"}, status_code=400)
        origin = request.headers.get("origin")
        if origin and origin not in allowed_origins:
            return JSONResponse({"detail": "origin not allowed"}, status_code=400)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    # A replacement interface (--static-dir) is served exactly like the bundled
    # one: same token, same Host/Origin checks, same CSP, no build step.
    page_dir = Path(static_dir) if static_dir is not None else STATIC_DIR
    if page_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(page_dir)), name="static")

    # -- page -------------------------------------------------------------- #
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, token: Optional[str] = None) -> HTMLResponse:
        page = (page_dir / "index.html").read_text(encoding="utf-8")
        cookie = request.cookies.get(COOKIE_NAME)
        if token and secrets.compare_digest(token, app.state.token):
            response = HTMLResponse(page)
            response.set_cookie(
                COOKIE_NAME,
                token,
                httponly=True,
                samesite="strict",
                path="/",
                secure=False,  # loopback http only; TLS is out of scope here
            )
            return response
        if cookie and secrets.compare_digest(cookie, app.state.token):
            return HTMLResponse(page)
        raise HTTPException(
            status_code=401,
            detail="open the URL printed by `uh web` (it carries the token)",
        )

    # -- status ------------------------------------------------------------ #
    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def status() -> dict:
        with _store(app) as store:
            sessions = len(store.list_sessions(limit=10_000))
            schema = store.schema_version
        return {
            "version": __version__,
            "provider": app.state.kind,
            "model": app.state.model,
            "provider_error": app.state.provider_error,
            "sessions": sessions,
            "store": str(app.state.settings.store_path),
            "schema_version": schema,
            "terminal": app.state.settings.allow_terminal,
            "workspace": str(app.state.settings.workspace),
        }

    @app.get("/api/commands", dependencies=[Depends(require_token)])
    async def list_commands() -> dict:
        return {
            "commands": [
                {
                    "name": command.name,
                    "aliases": list(command.aliases),
                    "help": command.help,
                    "args": command.args_hint,
                    "group": command.group,
                }
                for command in uh_commands.REGISTRY
            ]
        }

    # -- sessions ---------------------------------------------------------- #
    @app.get("/api/sessions", dependencies=[Depends(require_token)])
    async def sessions(limit: int = 50) -> dict:
        with _store(app) as store:
            rows = store.list_sessions(limit=limit)
        return {"sessions": [{**row, "short_id": display_id(row["id"])} for row in rows]}

    @app.post("/api/sessions", dependencies=[Depends(require_token)])
    async def create_session(payload: Optional[dict] = None) -> dict:
        payload = payload or {}
        with _store(app) as store:
            session_id = store.create_session(
                title=str(payload.get("title") or "")[:120],
                model=app.state.model,
                provider=app.state.kind,
            )
            session = store.get_session(session_id)
        if session is None:  # the row was just inserted; None means a broken store
            raise HTTPException(status_code=500, detail="session vanished after creation")
        return {"session": {**session, "short_id": display_id(session_id)}}

    @app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    async def delete_session(session_id: str) -> dict:
        session = _session_or_404(app, session_id)
        with _store(app) as store:
            deleted = store.delete_session(session["id"])
        app.state.session_models.pop(session["id"], None)
        # The messages cascade in SQLite; the files on disk do not. A deleted
        # conversation must not leave its uploads behind.
        files_removed = 0
        folder = attachments.session_dir(session["id"])
        if folder.is_dir():
            import shutil

            try:
                files_removed = sum(1 for item in folder.iterdir() if item.is_file())
                shutil.rmtree(folder)
            except OSError:
                files_removed = 0
        return {"deleted": deleted, "id": session["id"], "files_removed": files_removed}

    @app.patch("/api/sessions/{session_id}", dependencies=[Depends(require_token)])
    async def update_session(session_id: str, payload: dict) -> dict:
        """Rename a session, file it in a folder, or both.

        ``folder`` is metadata, not a schema: ``""`` (or null) takes the session
        out of its folder. A body with neither key is refused rather than
        silently accepted, so a typo in the client shows up as a 400.
        """
        session = _session_or_404(app, session_id)
        body = payload or {}
        if "title" not in body and "folder" not in body:
            raise HTTPException(status_code=400, detail="nothing to update: send title or folder")
        with _store(app) as store:
            if "title" in body:
                title = str(body.get("title") or "")[:120]
                store.rename_session(session["id"], title)
            if "folder" in body:
                store.set_folder(session["id"], str(body.get("folder") or ""))
            updated = store.get_session(session["id"])
        if updated is None:  # deleted concurrently: report it, do not invent a row
            raise HTTPException(status_code=404, detail=f"unknown session: {session_id}")
        # ``id``/``title`` stay at the top level: this route answered that shape
        # before folders existed, and a client written against it must not break.
        return {
            "id": updated["id"],
            "title": updated["title"],
            "folder": (updated.get("metadata") or {}).get("folder") or "",
            "session": {**updated, "short_id": display_id(updated["id"])},
        }

    # -- folders ----------------------------------------------------------- #
    # A folder is a value in session metadata, so these two routes are bulk
    # updates of one key. The list route exists because a folder with no session
    # in it does not exist at all: the sidebar cannot invent one.
    @app.get("/api/folders", dependencies=[Depends(require_token)])
    async def list_folders() -> dict:
        with _store(app) as store:
            counts = store.folders()
        return {"folders": [{"name": name, "count": count} for name, count in sorted(counts.items())]}

    def _folder_name(value: str) -> str:
        name = (value or "").strip()[:60]
        if not name:
            raise HTTPException(status_code=400, detail="the folder name is empty")
        if "/" in name:
            raise HTTPException(
                status_code=400,
                detail="a folder name cannot contain '/': it would be unreachable by URL",
            )
        return name

    @app.patch("/api/folders/{name}", dependencies=[Depends(require_token)])
    async def rename_folder(name: str, payload: dict) -> dict:
        target = _folder_name(str((payload or {}).get("name") or ""))
        if target == name.strip():
            raise HTTPException(status_code=400, detail="the folder already has that name")
        with _store(app) as store:
            moved = store.rename_folder(name, target)
        if not moved:
            raise HTTPException(status_code=404, detail=f"unknown folder: {name}")
        return {"name": target, "previous": name, "moved": moved}

    @app.delete("/api/folders/{name}", dependencies=[Depends(require_token)])
    async def delete_folder(name: str) -> dict:
        with _store(app) as store:
            cleared = store.delete_folder(name)
        if not cleared:
            raise HTTPException(status_code=404, detail=f"unknown folder: {name}")
        return {"deleted": name, "unfiled": cleared}

    @app.get("/api/sessions/{session_id}/messages", dependencies=[Depends(require_token)])
    async def messages(session_id: str, limit: Optional[int] = None) -> dict:
        session = _session_or_404(app, session_id)
        with _store(app) as store:
            rows = store.get_messages(session["id"], limit=limit)
        kind, model = app.state.session_models.get(
            session["id"], (app.state.kind, app.state.model)
        )
        return {
            "session": {**session, "short_id": display_id(session["id"])},
            "messages": rows,
            "provider": kind,
            "model": model,
        }

    @app.delete(
        "/api/sessions/{session_id}/messages/{message_id}",
        dependencies=[Depends(require_token)],
    )
    async def delete_message(session_id: str, message_id: int) -> dict:
        """Delete one message of one session: the answer that was wrong, not the day."""
        session = _session_or_404(app, session_id)
        with _store(app) as store:
            deleted = store.delete_message(session["id"], message_id)
            remaining = store.message_count(session["id"])
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"no message {message_id} in session {display_id(session['id'])}",
            )
        return {"deleted": message_id, "session_id": session["id"], "remaining": remaining}

    # -- attachments ------------------------------------------------------- #
    # Raw-body upload, not multipart: FastAPI needs ``python-multipart`` for form
    # uploads, which is not in the ``[web]`` extra. A Blob posted as the body with
    # the name in the query string needs neither the dependency nor a parser.
    @app.post("/api/sessions/{session_id}/upload", dependencies=[Depends(require_token)])
    async def upload(session_id: str, request: Request, name: str = "file") -> dict:
        session = _session_or_404(app, session_id)
        declared = int(request.headers.get("content-length") or 0)
        if declared > attachments.MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"the file is larger than {attachments.human_size(attachments.MAX_BYTES)}",
            )
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > attachments.MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "the file is larger than "
                        f"{attachments.human_size(attachments.MAX_BYTES)}"
                    ),
                )
            chunks.append(chunk)
        try:
            stored = attachments.save(session["id"], name, b"".join(chunks))
        except attachments.AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"attachment": stored.as_dict()}

    # -- chat (SSE) -------------------------------------------------------- #
    @app.post("/api/sessions/{session_id}/chat", dependencies=[Depends(require_token)])
    async def chat(session_id: str, payload: dict, request: Request) -> StreamingResponse:
        body = payload or {}
        text = str(body.get("message") or "").strip()
        raw_paths = [str(item) for item in (body.get("attachments") or [])]
        if not text and not raw_paths:
            raise HTTPException(status_code=400, detail="message is required")
        session = _session_or_404(app, session_id)
        try:
            # Inlined under 4 KB, referenced above it: the turn is pruned here,
            # before the history grows by a file.
            text = attachments.compose(text, raw_paths, session["id"])
        except attachments.AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            kind, model, provider = _provider_for(app, session["id"])
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[dict]" = asyncio.Queue()

        def emit(event: dict) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def work() -> None:
            store = None
            session_obj = None
            try:
                store = Store(app.state.settings.store_path)
                session_obj = ChatSession(
                    provider_factory=app.state.provider_factory,
                    provider_kind=kind,
                    chain=[(kind, provider)],
                    model=model,
                    toolsets=_toolsets(app),
                    workspace=app.state.settings.workspace,
                    cfg=app.state.cfg,
                    stream=True,
                    store=store,
                    max_iterations=max(1, int(app.state.cfg.get("max_iterations") or 8)),
                    session_id=session["id"],
                    announce=False,
                    console=Console(stream=io.StringIO(), err=io.StringIO()),
                )
                session_obj.events = emit
                app.state.active[session["id"]] = session_obj
                # A line starting with "/" is command intent, exactly as in the
                # console: the chips insert those lines, and sending "/tools" to
                # the model would be a question about the string "/tools".
                if text.startswith("/") and uh_commands.is_command(text):
                    result = uh_commands.dispatch(session_obj, text)
                    body = result.out or result.err or ""
                    if result.err:
                        emit({"type": "error", "message": result.err})
                    else:
                        emit({"type": "token", "text": body})
                        emit({"type": "answer", "content": body})
                else:
                    session_obj.run_text(text)
            except Exception as exc:  # noqa: BLE001 - reported to the browser
                emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                app.state.active.pop(session["id"], None)
                emit({"type": "__end__"})
                if store is not None:
                    store.close()

        threading.Thread(target=work, daemon=True, name=f"uh-chat-{session['id']}").start()

        async def stream():
            yield frame("open", {"session": display_id(session["id"]), "model": model})
            while True:
                event = await queue.get()
                if event.get("type") == "__end__":
                    yield frame("done", {"session": display_id(session["id"])})
                    return
                kind_name = event.pop("type", "message")
                yield frame(kind_name, event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/sessions/{session_id}/stop", dependencies=[Depends(require_token)])
    async def stop(session_id: str) -> dict:
        session = _session_or_404(app, session_id)
        active = app.state.active.get(session["id"])
        if active is None:
            return {"stopped": False, "reason": "no turn in progress"}
        active.request_stop()
        return {"stopped": True}

    # -- UHB proxy --------------------------------------------------------- #
    # The browser never talks to UHB: CSP stays 'self', and UH is the only thing
    # that knows the loopback URL. Two read-only routes, no token spent.
    def _uhb() -> tuple:
        from ..toolsets.uhb import CALL_TIMEOUT, TIMEOUT, Uhbtoolset, resolve_url

        url = resolve_url()
        if url is None:
            raise HTTPException(
                status_code=503,
                detail="UHB is disabled: UH_UHB_URL is empty or not a loopback URL",
            )
        return Uhbtoolset(url, timeout=CALL_TIMEOUT), url, TIMEOUT

    @app.get("/api/uhb/status", dependencies=[Depends(require_token)])
    async def uhb_state() -> dict:
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        url = resolve_url()
        if url is None:
            return {
                "connected": False,
                "reason": "UHB is disabled (UH_UHB_URL is empty or not loopback)",
            }
        payload = Uhbtoolset(url, timeout=1.0).status()
        if "error" in payload:
            return {"connected": False, "reason": str(payload.get("error") or "unreachable")}
        return {"connected": True, **payload}

    @app.post("/api/uhb/route", dependencies=[Depends(require_token)])
    async def uhb_plan(payload: dict) -> dict:
        prompt = str((payload or {}).get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt is required")
        tool, _url, _timeout = _uhb()
        plan: dict = tool.route(
            prompt,
            str((payload or {}).get("mode") or ""),
            str((payload or {}).get("complexity") or ""),
        )
        if "error" in plan:
            raise HTTPException(status_code=503, detail=str(plan.get("error")))
        return plan

    # -- dreams ------------------------------------------------------------ #
    @app.get("/api/dreams", dependencies=[Depends(require_token)])
    async def dreams() -> dict:
        from .. import dream_store

        state = {}
        try:
            from .. import dream_daemon

            state = dream_daemon.read_status()
        except Exception:  # noqa: BLE001 - a missing daemon is not an error
            state = {"state": "stopped"}
        return {"dreams": dream_store.list_dreams(), "daemon": state}

    @app.get("/api/dreams/{stamp}", dependencies=[Depends(require_token)])
    async def dream_one(stamp: str) -> dict:
        from .. import dream_store

        entry = dream_store.read_dream(stamp)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown dream")
        return entry

    @app.delete("/api/dreams/{stamp}", dependencies=[Depends(require_token)])
    async def dream_delete(stamp: str) -> dict:
        from .. import dream_store

        if not dream_store.delete(stamp):
            raise HTTPException(status_code=404, detail="unknown dream")
        return {"deleted": stamp}

    @app.post("/api/dreams/{stamp}/archive", dependencies=[Depends(require_token)])
    async def dream_archive(stamp: str) -> dict:
        from .. import dream_store

        path = dream_store.archive(stamp)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown dream")
        return {"archived": stamp, "path": path}

    @app.post("/api/dreams/{stamp}/fulfill", dependencies=[Depends(require_token)])
    async def dream_fulfill(stamp: str) -> dict:
        """Write a plan of action. Applying it stays a human decision."""
        from .. import dream_store

        path = dream_store.fulfill(stamp)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown dream")
        return {"stamp": stamp, "plan": path, "applied": False}

    @app.post("/api/dreams/daemon/{action}", dependencies=[Depends(require_token)])
    async def dream_daemon_control(action: str) -> dict:
        """Start or stop the idle daemon. Only an explicit click starts it."""
        import subprocess
        import sys

        from .. import dream_daemon

        if action == "stop":
            dream_daemon.stop()
            return {"daemon": "stopping"}
        if action != "start":
            raise HTTPException(status_code=400, detail="action must be start or stop")
        if not dream_daemon.running():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
            subprocess.Popen(  # noqa: S603 - our own module, no shell
                [sys.executable, "-m", "uh.cli", "dream", "--daemon"],
                creationflags=flags,
            )
        return {"daemon": "starting"}

    @app.get("/api/system/ollama", dependencies=[Depends(require_token)])
    async def system_ollama() -> dict:
        """Always 200: Ollama is optional, and a 404 would read like a failure.

        The UI needs three states - running, installed but silent, absent - and a
        reason for each. A missing service is not an error.
        """
        from .. import ollama_spawn

        return ollama_spawn.status()

    # -- decision ---------------------------------------------------------- #
    @app.get("/api/sessions/{session_id}/decision", dependencies=[Depends(require_token)])
    async def session_decision(session_id: str) -> dict:
        """Why the last turn looked the way it did. Facts only, no reconstruction."""
        from .. import decision as uh_decision

        with _store(app) as store:
            if store.get_session(session_id) is None:
                raise HTTPException(status_code=404, detail="unknown session")
            return uh_decision.payload(store, session_id)

    # -- memory ------------------------------------------------------------ #
    # Read-only views of what UH remembers. No route here writes, and none of
    # them invents a RAG/SiYuan endpoint that UHB does not expose.
    @app.get("/api/memory/sources", dependencies=[Depends(require_token)])
    async def memory_sources() -> dict:
        from .. import memory_view

        data = memory_view.sources()
        # UHB knows what the RAG service holds; our disk scan only sees the
        # checkout. When UHB answers, its view wins and `source` says so.
        try:
            from ..toolsets.uhb import resolve_url

            url = resolve_url()
            if url is not None:
                from .. import memory_actions

                asked = memory_actions.rag_status(url)
                if asked["status"] == 200 and asked["payload"]:
                    data["rag"] = {**asked["payload"], "via": "uhb"}
        except Exception:  # noqa: BLE001 - a memory view never fails a load
            pass
        with _store(app):
            # Opening the store proves it is readable; the count is what the tab shows.
            data["sessions"] = {
                "available": True,
                "store": str(app.state.settings.store_path),
            }
        return data

    @app.get("/api/memory/wiki", dependencies=[Depends(require_token)])
    async def memory_wiki() -> dict:
        from .. import memory_view

        found = memory_view.wiki_root()
        return {
            "available": found is not None,
            "label": found[0] if found else None,
            "root": str(found[1]) if found else None,
            "notes": [note.as_dict() for note in memory_view.wiki_notes()],
        }

    @app.get("/api/memory/wiki/{note:path}", dependencies=[Depends(require_token)])
    async def memory_wiki_note(note: str) -> dict:
        from .. import memory_view

        try:
            return memory_view.read_note(note)
        except memory_view.MemoryViewError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/memory/rag", dependencies=[Depends(require_token)])
    async def memory_rag() -> dict:
        from .. import memory_view

        return memory_view.rag_state()

    @app.get("/api/memory/siyuan", dependencies=[Depends(require_token)])
    async def memory_siyuan() -> dict:
        from .. import memory_view

        return memory_view.siyuan_state()

    # -- memory actions ----------------------------------------------------- #
    # The two routes below are the first that act instead of observing. They are
    # proxies: the browser stays on 'self', UH is the only one that knows
    # UH_UHB_URL, and UHB's own sentence is passed through instead of rewritten.
    def _uhb_url() -> str:
        from ..toolsets.uhb import resolve_url

        url = resolve_url()
        if url is None:
            raise HTTPException(
                status_code=502,
                detail="UHB is disabled: UH_UHB_URL is empty or not a loopback URL",
            )
        return url

    @app.post("/api/memory/rag/reload", dependencies=[Depends(require_token)])
    async def memory_rag_reload() -> JSONResponse:
        from .. import memory_actions

        result = memory_actions.rag_reload(_uhb_url())
        return JSONResponse(status_code=int(result["status"]), content=result["payload"])

    @app.get("/api/memory/siyuan/blocks", dependencies=[Depends(require_token)])
    async def memory_siyuan_blocks(path: str = "", limit: int = 20) -> JSONResponse:
        from .. import memory_actions

        result = memory_actions.siyuan_blocks(_uhb_url(), path=path, limit=limit)
        return JSONResponse(status_code=int(result["status"]), content=result["payload"])

    @app.get("/api/memory/search", dependencies=[Depends(require_token)])
    async def memory_search(q: str = "", limit: int = 20) -> dict:
        """Substring search in the memory sources, plus FTS5 across the sessions."""
        from .. import memory_view

        query = (q or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="q is required")
        hits = memory_view.search_memory(query, limit=limit)
        with _store(app) as store:
            for row in store.search(query, limit=limit):
                hits.append(
                    {
                        "kind": "session",
                        "title": row.get("title") or row.get("session_id"),
                        "path": display_id(str(row.get("session_id"))),
                        "snippet": row.get("snippet") or "",
                    }
                )
        return {"query": query, "hits": hits[:limit]}

    # -- system ------------------------------------------------------------ #
    @app.get("/api/system/capabilities", dependencies=[Depends(require_token)])
    async def capabilities() -> dict:
        """What this machine can run, and what it should run.

        Read-only, no subprocess unless a GPU tool is on PATH: the detection is
        cached for the process, so a polling UI costs nothing after the first call.
        """
        from .. import hardware

        found = hardware.profile()
        return {**found.as_dict(), "sentence": found.sentence()}

    @app.get("/api/telemetry", dependencies=[Depends(require_token)])
    async def telemetry() -> dict:
        """What was spent, and what the machine is doing right now.

        Read-only. Tokens come from the decisions already recorded on the
        sessions, passes from ``dreams/schedule.log``, and CPU/RAM/VRAM from the
        OS - the whole reading runs in a thread because two kernel samples 120 ms
        apart inside the event loop would stall every other request.
        """
        from .. import telemetry as uh_telemetry

        with _store(app) as store:
            sessions = store.list_sessions(limit=1000)
        return await asyncio.to_thread(uh_telemetry.snapshot, sessions)

    # -- data: backup, skills, tools (P8) ---------------------------------- #
    # The browser holds no state that matters: conversations are in SQLite under
    # $UH_HOME, and this is how they leave the machine - one archive the user can
    # move, keep, or restore. No cloud, no account, no browser storage.
    @app.get("/api/backup", dependencies=[Depends(require_token)])
    async def backup_list() -> dict:
        from .. import backup as uh_backup

        return {"backups": uh_backup.list_backups(), "dir": str(uh_backup.backup_dir())}

    @app.post("/api/backup/export", dependencies=[Depends(require_token)])
    async def backup_export() -> dict:
        """Write the archive in `$UH_HOME/backups/` and describe it."""
        from .. import backup as uh_backup

        try:
            return await asyncio.to_thread(uh_backup.export)
        except uh_backup.BackupError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/backup/download/{name}", dependencies=[Depends(require_token)])
    async def backup_download(name: str) -> FileResponse:
        """The browser cannot read a local path: it asks for the file by name."""
        from .. import backup as uh_backup

        path = uh_backup.resolve_backup(name)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown backup")
        return FileResponse(path, media_type="application/zip", filename=path.name)

    @app.post("/api/backup/import", dependencies=[Depends(require_token)])
    async def backup_import(request: Request) -> dict:
        """Restore an archive sent as the raw body, after snapshotting this home.

        Raw body, not multipart: one file, no form parsing, and no dependency
        (``python-multipart``) that the rest of UH does not need.
        """
        import tempfile

        from .. import backup as uh_backup

        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_IMPORT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"archive larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MB",
                )
            chunks.append(chunk)
        if not total:
            raise HTTPException(status_code=400, detail="send the zip as the request body")
        with tempfile.TemporaryDirectory(prefix="uh-upload-") as staging:
            path = Path(staging) / "upload.zip"
            path.write_bytes(b"".join(chunks))
            try:
                return await asyncio.to_thread(uh_backup.import_backup, path)
            except uh_backup.BackupError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/skills", dependencies=[Depends(require_token)])
    async def skills_list() -> dict:
        from .. import skills as uh_skills

        return uh_skills.list_skills()

    @app.post("/api/skills", dependencies=[Depends(require_token)])
    async def skills_create(payload: dict) -> dict:
        from .. import skills as uh_skills

        body = payload or {}
        try:
            return uh_skills.create(
                title=str(body.get("title") or ""),
                description=str(body.get("description") or ""),
                instructions=str(body.get("instructions") or ""),
                name=(str(body["name"]) if body.get("name") else None),
            )
        except uh_skills.SkillError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tools/status", dependencies=[Depends(require_token)])
    async def tools_status() -> dict:
        """What is connected, as far as this process can actually tell.

        No invented green light: a toolset is listed because it was built, UHB is
        connected because it answered, and MCP says plainly that there is no
        client yet - a red dot that means "not supported" is honest, a green one
        that means "we hope" is not.
        """
        from .. import ollama_spawn
        from ..toolsets import build_toolsets
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        toolsets: dict = {}
        try:
            for name, toolset in build_toolsets(app.state.settings.workspace).items():
                toolsets[name] = [
                    spec.get("function", {}).get("name")
                    for spec in getattr(toolset, "tools", None) or []
                    if isinstance(spec, dict)
                ]
        except Exception:  # noqa: BLE001 - a broken toolset is a missing one
            toolsets = {}

        url = resolve_url()
        if url is None:
            uhb = {"connected": False, "reason": "UHB is disabled (UH_UHB_URL unset or not loopback)"}
        else:
            payload = await asyncio.to_thread(lambda: Uhbtoolset(url, timeout=1.0).status())
            if "error" in payload:
                uhb = {"connected": False, "reason": str(payload.get("error") or "unreachable")}
            else:
                uhb = {"connected": True, **payload}

        return {
            "toolsets": toolsets,
            "ollama": await asyncio.to_thread(ollama_spawn.status),
            "uhb": uhb,
            "mcp": {
                "supported": False,
                "reason": "UH has no MCP client yet: no MCP server can be attached",
            },
            "terminal": app.state.settings.allow_terminal,
            "workspace": str(app.state.settings.workspace),
        }

    # -- search / models --------------------------------------------------- #
    @app.get("/api/search", dependencies=[Depends(require_token)])
    async def search(q: str, limit: int = 20) -> dict:
        with _store(app) as store:
            hits = store.search(q, limit=limit)
        return {"query": q, "hits": [{**hit, "short_id": display_id(hit["session_id"])} for hit in hits]}

    @app.get("/api/models", dependencies=[Depends(require_token)])
    async def models() -> dict:
        from ..providers import all_provider_kinds

        local: list[str] = []
        try:
            from ..providers.ollama import list_models

            local = [entry["name"] for entry in list_models(timeout=1.5)]
        except ProviderError:
            local = []
        return {
            "provider": app.state.kind,
            "model": app.state.model,
            "ollama": local,
            "providers": all_provider_kinds(),
            "pulling": sorted(app.state.pulling),
        }

    @app.post("/api/models/pull", dependencies=[Depends(require_token)])
    async def pull_model_endpoint(payload: dict) -> dict:
        """Start `ollama pull` in a background thread; the UI polls /api/models.

        Two at a time at most: three concurrent pulls saturate a home connection
        and the browser cannot show progress for any of them anyway.
        """
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        if name in app.state.pulling:
            return {"started": False, "name": name, "reason": "already pulling"}
        if len(app.state.pulling) >= 2:
            raise HTTPException(status_code=429, detail="two downloads are already running")
        app.state.pulling.add(name)

        def work() -> None:
            from ..providers.ollama import pull_model

            try:
                pull_model(name, on_event=lambda event: None)
            except ProviderError:
                pass  # a failed pull is visible as "not installed", not as a crash
            finally:
                app.state.pulling.discard(name)

        threading.Thread(target=work, daemon=True, name=f"uh-pull-{name}").start()
        return {"started": True, "name": name}

    @app.post("/api/model", dependencies=[Depends(require_token)])
    async def set_model(payload: dict) -> dict:
        model = str((payload or {}).get("model") or "").strip()
        kind = str((payload or {}).get("provider") or app.state.kind or "").strip()
        session_id = (payload or {}).get("session_id")
        if not model:
            raise HTTPException(status_code=400, detail="model is required")
        try:
            app.state.provider_factory(kind, model)
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if session_id:
            session = _session_or_404(app, str(session_id))
            app.state.session_models[session["id"]] = (kind, model)
            return {"session": display_id(session["id"]), "provider": kind, "model": model}
        app.state.kind, app.state.model = kind, model
        return {"provider": kind, "model": model}

    # Jalon 7a: provider panel. Imported late to keep the module import light.
    from .providers_api import register_provider_routes

    register_provider_routes(app, require_token)

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    allow_terminal: bool = False,
    workspace: Optional[Path] = None,
    static_dir: Optional[Path] = None,
) -> int:
    """Start uvicorn and print the tokenised URL. Returns the exit code."""
    import uvicorn

    app = create_app(
        host=host,
        port=port,
        allow_terminal=allow_terminal,
        workspace=workspace,
        static_dir=static_dir,
    )
    url = f"http://{host}:{port}/?token={app.state.token}"
    print(f"UH web - {url}")
    print(f"  provider: {app.state.kind or 'none'} · model: {app.state.model or 'none'}")
    print(f"  workspace: {app.state.settings.workspace}")
    if static_dir is not None:
        print(f"  interface: {static_dir} (replacement)")
    if not allow_terminal:
        print("  terminal toolset: disabled (enable with --allow-terminal)")
    if host not in LOOPBACK_NAMES:
        print(
            f"  WARNING: listening on {host}, not loopback - anyone who reaches this port "
            "can use the token from this URL. Bind 127.0.0.1 unless you mean it.",
        )
    print("  Ctrl-C to stop")
    if open_browser:
        threading.Timer(1.0, _open_browser, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box has no browser
        pass
