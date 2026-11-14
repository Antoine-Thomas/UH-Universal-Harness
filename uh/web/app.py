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
import re
import secrets
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Callable, Iterator, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request
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


def _toolsets(app: FastAPI, web_search: bool = True) -> dict:
    toolsets = build_toolsets(app.state.settings.workspace)
    if not app.state.settings.allow_terminal:
        toolsets.pop("terminal", None)
    if not web_search:
        # The toggle in the conversation settings, not a provider parameter: the
        # tool simply is not offered to the model for this conversation.
        toolsets.pop("web_search", None)
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
#: A short list, not a catalogue: the router accepts any repo id, and a dropdown
#: of ten thousand entries helps nobody. Each one is a chat model that ships tools.
_HF_MODELS = (
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "mistralai/Mistral-7B-Instruct-v0.3",
    "meta-llama/Llama-3.1-8B-Instruct",
    "HuggingFaceH4/zephyr-7b-beta",
)


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
        # A stale server serving a newer interface is how a panel ends up calling
        # routes that do not exist ("Not Found" on a button that exists in the
        # code). The build stamp makes the mismatch visible to the client, and the
        # query string keeps a cached app.js from outliving a restart.
        page = page.replace("{{BUILD}}", __version__).replace("{{VERSION}}", __version__)
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
        if "title" not in body and "folder" not in body and "settings" not in body:
            raise HTTPException(
                status_code=400, detail="nothing to update: send title, folder or settings"
            )
        with _store(app) as store:
            if "title" in body:
                title = str(body.get("title") or "")[:120]
                store.rename_session(session["id"], title)
            if "folder" in body:
                store.set_folder(session["id"], str(body.get("folder") or ""))
            if "settings" in body:
                # Stored on the session, not in the browser: a setting that
                # changes an answer belongs with the conversation it changed.
                blob = body.get("settings") or {}
                values = blob.get("values") if isinstance(blob, dict) else None
                metadata = dict(session.get("metadata") or {})
                metadata["settings"] = {
                    "enabled": bool((blob or {}).get("enabled")),
                    "values": values if isinstance(values, dict) else {},
                }
                store.set_session_metadata(session["id"], metadata)
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
    @app.get("/api/llm/settings", dependencies=[Depends(require_token)])
    async def llm_settings(session_id: Optional[str] = None) -> dict:
        """The knobs, the bounds, and what this provider will actually honour.

        The panel draws itself from this payload: one table in Python
        (``uh/llm_params.py``) is the only place that knows a slider's range, and
        the support matrix is reported rather than guessed in the browser.
        """
        from .. import llm_params

        kind, model = app.state.kind, app.state.model
        if session_id:
            session = _session_or_404(app, session_id)
            kind, model = app.state.session_models.get(session["id"], (kind, model))
        return {
            "provider": kind,
            "model": model,
            "support": llm_params.describe(kind, model),
            **llm_params.public(),
        }

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
        # A skill is attached to this turn only: the block rides on the system
        # message, so the transcript keeps saying who said what.
        skill_name = str(body.get("skill") or "").strip()
        skill_block = ""
        if skill_name:
            from .. import skills as uh_skills

            skill_block = uh_skills.prompt_block(skill_name) or ""
            if not skill_block:
                raise HTTPException(status_code=400, detail=f"unknown skill: {skill_name}")
        # Conversation settings: clamped here, never trusted from the browser, and
        # empty when the panel is off - an untouched slider must not change an
        # answer that used to come from the provider's own defaults.
        from .. import llm_params

        settings = body.get("settings") or {}
        try:
            chat_params = llm_params.sanitise(settings)
            search_on = llm_params.web_search_enabled(settings)
        except Exception as exc:  # noqa: BLE001 - a malformed blob is a 400, not a 500
            raise HTTPException(status_code=400, detail=f"invalid settings: {exc}") from exc
        toolsets = _toolsets(app, web_search=search_on)

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
                    toolsets=toolsets,
                    workspace=app.state.settings.workspace,
                    cfg=app.state.cfg,
                    stream=True,
                    store=store,
                    max_iterations=max(1, int(app.state.cfg.get("max_iterations") or 8)),
                    session_id=session["id"],
                    announce=False,
                    console=Console(stream=io.StringIO(), err=io.StringIO()),
                    extra_system=skill_block,
                    params=chat_params,
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
            yield frame(
                "open",
                {
                    "session": display_id(session["id"]),
                    "model": model,
                    "skill": skill_name,
                    "settings": chat_params,
                },
            )
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
        from .. import uhb_spawn
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        url = resolve_url()
        if url is None:
            return {
                "connected": False,
                "reason": "UHB is disabled (UH_UHB_URL is empty or not loopback)",
            }
        payload = Uhbtoolset(url, timeout=1.0).status()
        if "error" in payload:
            return {
                "connected": False,
                "reason": str(payload.get("error") or "unreachable"),
                # The panel offers "Détails": the log is what actually explains a
                # failed start, and the user should not have to find it.
                "log": uhb_spawn.log_tail(),
                "checkout": str(uhb_spawn.find_checkout() or ""),
            }
        return {"connected": True, **payload}

    @app.post("/api/uhb/start", dependencies=[Depends(require_token)])
    async def uhb_start() -> dict:
        """Start the second brain from the panel: the button, not a shell command.

        ``uh web`` already spawns UHB at startup, so this covers the two other
        cases: it died during the session, or the panel belongs to a window whose
        process is not the one that spawned it. Off the event loop (a cold start
        takes seconds) and with the same budget as the CLI.
        """
        from .. import uhb_spawn
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        url = resolve_url()
        if url is None:
            raise HTTPException(
                status_code=400,
                detail="UHB is disabled: UH_UHB_URL is empty or not loopback",
            )
        try:
            await asyncio.to_thread(lambda: uhb_spawn.ensure(timeout=25.0))
        except uhb_spawn.SpawnError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        payload = await asyncio.to_thread(lambda: Uhbtoolset(url, timeout=2.0).status())
        if "error" in payload:
            return {
                "connected": False,
                "reason": str(payload.get("error") or "still unreachable"),
                "log": uhb_spawn.log_tail(),
                "checkout": str(uhb_spawn.find_checkout() or ""),
            }
        return {"connected": True, **payload}

    @app.post("/api/uhb/stop", dependencies=[Depends(require_token)])
    async def uhb_stop() -> dict:
        """Stop the UHB this process started. Chat and tools keep working without it."""
        from .. import uhb_spawn

        child = uhb_spawn.running()
        await asyncio.to_thread(uhb_spawn.stop_running)
        return {"connected": False, "stopped": child is not None}

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

    @app.post("/api/dreams/run", dependencies=[Depends(require_token)])
    async def dream_run(payload: Optional[dict] = None) -> dict:
        """One pass, now, in its own process. Never twice, never on the event loop.

        A pass reads sources and may spend a provider call: running it inside the
        server would freeze every panel for minutes, and a second click would pay
        twice. The child inherits ``UH_HOME``, writes its report where the daemon
        does, and its output goes to ``$UH_HOME/dreams/manual-run.log`` - the one
        place a failed start can be read afterwards.
        """
        import subprocess
        import sys
        from datetime import datetime

        from ..toolsets.memory import uh_home

        body = payload or {}
        try:
            budget = int(body.get("budget") or 40000)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="budget must be an integer") from None
        budget = max(1000, min(budget, 200000))

        previous = getattr(app.state, "dream_run", None)
        if previous is not None and previous.poll() is None:
            raise HTTPException(status_code=409, detail="a dream pass is already running")

        log = uh_home() / "dreams" / "manual-run.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        workspace = app.state.settings.workspace
        with log.open("ab") as handle:
            handle.write(
                f"\n--- {datetime.now().isoformat(timespec='seconds')} pass from the cockpit ---\n"
                .encode("utf-8")
            )
            handle.flush()
            process = subprocess.Popen(  # noqa: S603 - our own module, no shell
                [
                    sys.executable, "-m", "uh.cli", "dream",
                    "--now", "--budget", str(budget), "--yes",
                ],
                stdout=handle,
                stderr=handle,
                creationflags=flags,
                cwd=str(workspace) if workspace else None,
            )
        app.state.dream_run = process
        return {"started": True, "pid": process.pid, "budget": budget, "log": str(log)}

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
    async def dream_fulfill(stamp: str, upload: str = "") -> dict:
        """Write a plan of action, optionally storing it on a connected cloud.

        Applying it stays a human decision; the upload is the operator's choice,
        taken with the button ("Exaucer et uploader sur AWS S3"). A cloud that
        refuses is reported next to the plan, never instead of it.
        """
        from .. import dream_store

        path = dream_store.fulfill(stamp)
        if path is None:
            raise HTTPException(status_code=404, detail="unknown dream")

        pushed: dict[str, object] = {"requested": bool(upload), "ok": False}
        if upload:
            from .. import mcp_cloud

            targets = {item["name"]: item for item in await asyncio.to_thread(mcp_cloud.upload_targets)}
            target = targets.get(upload)
            if target is None:
                pushed["error"] = f"'{upload}' is not a cloud server that can store a file"
            else:
                from .. import mcp_cloud as cloud

                pushed = await asyncio.to_thread(
                    cloud.upload_plan, target["name"], target["tool"], Path(path)
                )
        return {"stamp": stamp, "plan": path, "applied": False, "upload": pushed}

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

    @app.get("/api/memory/graph", dependencies=[Depends(require_token)])
    async def memory_graph() -> dict:
        """Where the memory lives, and what actually reaches a turn.

        Counts come from the same sources the Memory tab reads. An edge is
        ``wired`` only when UH really consults that source for a turn: the wiki is
        searchable from the UI but no tool reads it, RAG has no route, SiYuan goes
        through a UHB that is not asked. Drawing those three as connected would be
        a prettier lie than a sentence, so they are dashed and labelled.
        """
        from .. import dream_store, memory_view

        sources = memory_view.sources()
        wiki = sources.get("wiki") or {}
        memory = sources.get("memory") or {}
        rag = sources.get("rag") or {}
        siyuan = sources.get("siyuan") or {}
        dreams = dream_store.list_dreams()
        with _store(app) as store:
            sessions = store.list_sessions(limit=10_000)
        messages = sum(int(row.get("message_count") or 0) for row in sessions)
        ideas = sum(len(dream.get("ideas") or []) for dream in dreams)
        memory_available = bool(memory.get("available"))
        wiki_available = bool(wiki.get("available"))
        rag_available = bool(rag.get("available"))

        def state(available: bool, count: object) -> str:
            """``on`` has content, ``empty`` exists but holds nothing, ``off`` is
            not readable at all. ``sections`` on a missing MEMORY.md is the list
            of *default* sections, which is not content: showing "3 sections"
            next to an absent file would be the kind of detail that makes a
            diagram lie."""
            if not available:
                return "off"
            return "on" if count else "empty"

        nodes = [
            {
                "id": "memory",
                "label": "MEMORY.md",
                "column": "in",
                "count": len(memory.get("sections") or []) if memory_available else None,
                "unit": "section(s)",
                "state": state(memory_available, memory.get("sections") if memory_available else None),
                "detail": str(
                    memory.get("path")
                    if memory_available
                    else "MEMORY.md absent : le fichier s'écrit au premier `memory_write`"
                ),
            },
            {
                "id": "wiki",
                "label": "Wiki",
                "column": "in",
                "count": int(wiki.get("notes") or 0),
                "unit": "note(s)",
                "state": state(wiki_available, wiki.get("notes")),
                "detail": str(wiki.get("path") or wiki.get("reason") or ""),
            },
            {
                "id": "rag",
                "label": "RAG",
                "column": "in",
                "count": rag.get("chunks"),
                "unit": "chunk(s)",
                "state": state(rag_available, rag.get("chunks")),
                "detail": str(rag.get("reason") or "index présent"),
            },
            {
                "id": "siyuan",
                "label": "SiYuan",
                "column": "in",
                "count": None,
                "unit": "bloc(s)",
                "state": "off",
                "detail": str(siyuan.get("reason") or ""),
            },
            {
                "id": "turn",
                "label": "Tour UH",
                "column": "core",
                "count": None,
                "unit": "",
                "state": "on",
                "detail": "prompt système + contexte + outils",
            },
            {
                "id": "sessions",
                "label": "Sessions",
                "column": "out",
                "count": len(sessions),
                "unit": "session(s)",
                "state": state(True, sessions),
                "detail": f"{messages} message(s) en base",
            },
            {
                "id": "dreams",
                "label": "Rêves",
                "column": "out",
                "count": len(dreams),
                "unit": "passe(s)",
                "state": state(True, dreams),
                "detail": f"{ideas} idée(s) proposée(s)",
            },
        ]
        edges = [
            {"from": "memory", "to": "turn", "label": "outil memory", "wired": True},
            {"from": "sessions", "to": "turn", "label": "historique", "wired": True},
            {"from": "dreams", "to": "turn", "label": "propositions", "wired": False},
            {"from": "wiki", "to": "turn", "label": "pas d'outil", "wired": False},
            {"from": "rag", "to": "turn", "label": "sans route", "wired": False},
            {"from": "siyuan", "to": "turn", "label": "via UHB", "wired": False},
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "wired": sum(1 for edge in edges if edge["wired"]),
        }

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
        connected because it answered, and an MCP server is ``connected`` only
        when a process is actually attached to this harness - ``idle`` means
        "configured, not started", which is honest, and a green dot that means
        "we hope" is not.

        Building the toolsets reads the disk and, for MCP, may start a process:
        the work runs off the event loop so a slow server cannot freeze the UI.
        """
        from .. import mcp, ollama_spawn
        from ..toolsets import build_toolsets
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        toolsets: dict = {}
        try:
            built = await asyncio.to_thread(build_toolsets, app.state.settings.workspace)
            for name, toolset in built.items():
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

        mcp_status = await asyncio.to_thread(mcp.status)
        if not mcp_status.get("configured"):
            # An HTTP/SSE entry exists but the stdio client cannot start it: say that
            # instead of "nothing configured", which would be false.
            from .. import mcp_cloud

            pending = await asyncio.to_thread(mcp_cloud.pending_http_servers)
            if pending:
                mcp_status = {
                    **mcp_status,
                    "configured": len(pending),
                    "reason": (
                        f"{len(pending)} serveur(s) HTTP/SSE configuré(s), non démarré(s) : "
                        "le client MCP de UH est stdio (transport HTTP/SSE en P14.1)"
                    ),
                }
        return {
            "toolsets": toolsets,
            "ollama": await asyncio.to_thread(ollama_spawn.status),
            "uhb": uhb,
            "mcp": mcp_status,
            "terminal": app.state.settings.allow_terminal,
            "workspace": str(app.state.settings.workspace),
        }

    # -- self-repair ------------------------------------------------------- #
    @app.post("/api/fix", dependencies=[Depends(require_token)])
    async def fix_propose(payload: Annotated[dict, Body()]) -> dict:
        """Ask for a patch. Nothing is written to the tree: the diff is a proposal."""
        from .. import fixes

        body = payload or {}
        instruction = str(body.get("instruction") or "").strip()
        if not instruction:
            raise HTTPException(status_code=400, detail="describe what is broken")
        try:
            proposal = await asyncio.to_thread(
                fixes.propose, instruction, repo=app.state.settings.workspace
            )
        except fixes.FixError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {**proposal.summary(), "diff": proposal.diff}

    @app.post("/api/fix/{stamp}/apply", dependencies=[Depends(require_token)])
    async def fix_apply(stamp: str) -> dict:
        """Apply a recorded proposal on `fix/auto-<stamp>`, then run the linter.

        The proposal is re-read from disk and re-checked, never trusted from the
        request: a client could otherwise ask to apply anything.
        """
        from .. import fixes

        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}", stamp):
            raise HTTPException(status_code=400, detail="not a proposal timestamp")
        path = fixes.fixes_dir() / f"{stamp}.diff"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="unknown proposal")
        text = path.read_text(encoding="utf-8")
        repo = app.state.settings.workspace
        applies, error = await asyncio.to_thread(fixes.check, repo, text)
        if not applies:
            raise HTTPException(status_code=409, detail=f"the patch does not apply: {error}")
        proposal = fixes.Proposal(
            stamp=stamp, instruction="", diff=text, path=path, applies=True
        )
        checks = [[sys.executable, "-m", "ruff", "check", "uh", "tests"]]
        try:
            outcome = await asyncio.to_thread(fixes.apply, proposal, repo=repo, checks=checks)
        except fixes.FixError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return outcome

    # -- first run --------------------------------------------------------- #
    @app.get("/api/setup", dependencies=[Depends(require_token)])
    async def setup_state() -> dict:
        """Is the machine able to answer a turn at all, and what would it take?"""
        from .. import ollama_spawn
        from ..config import load as load_config
        from ..providers_admin import catalog, read_providers

        cfg = load_config()
        provider = str(cfg.get("default_provider") or "")
        ollama = await asyncio.to_thread(ollama_spawn.status)
        configured = read_providers()
        keyed = sorted(name for name, entry in configured.items() if entry.get("keys"))
        need = not bool(ollama.get("running")) and not keyed
        return {
            "needed": need,
            "provider": provider,
            "model": str(cfg.get("default_model") or ""),
            "ollama": {
                "running": bool(ollama.get("running")),
                "installed": bool(ollama.get("installed")),
                "models": list(ollama.get("models") or [])[:12],
            },
            "cloud": sorted(catalog()),
            "huggingface_models": _HF_MODELS,
        }

    @app.post("/api/setup", dependencies=[Depends(require_token)])
    async def setup_apply(payload: Annotated[dict, Body()]) -> dict:
        """Record one choice: a provider, optionally a key, optionally a model."""
        from ..config import write_layer
        from ..providers_admin import add_key_to, catalog

        body = payload or {}
        choice = str(body.get("choice") or "").strip().lower()
        model = str(body.get("model") or "").strip()
        key = str(body.get("key") or "").strip()
        if choice == "ollama":
            provider = "ollama"
        elif choice == "huggingface":
            provider = "huggingface"
            if key:
                add_key_to("huggingface", key)
        elif choice == "cloud":
            provider = str(body.get("provider") or "").strip().lower()
            if provider not in catalog():
                raise HTTPException(status_code=400, detail=f"unknown provider '{provider}'")
            if not key:
                raise HTTPException(status_code=400, detail="a cloud provider needs a key")
            add_key_to(provider, key)
        else:
            raise HTTPException(status_code=400, detail="choice must be ollama, huggingface or cloud")

        write_layer("user", "default_provider", provider)
        if model:
            write_layer("user", "default_model", model)
        return {"saved": True, "provider": provider, "model": model}

    @app.get("/api/mcp/cloud", dependencies=[Depends(require_token)])
    async def mcp_cloud_catalog() -> dict:
        """The templates the UI may offer, and which servers can already store a file."""
        from .. import mcp_cloud

        return {
            "templates": mcp_cloud.template_list(),
            "upload_targets": await asyncio.to_thread(mcp_cloud.upload_targets),
            # Recorded but not started: HTTP/SSE is the P14.1 transport.
            "http_servers": await asyncio.to_thread(mcp_cloud.pending_http_servers),
            "http_note": mcp_cloud.HTTP_NOTE,
        }

    @app.post("/api/mcp/cloud", dependencies=[Depends(require_token)])
    async def mcp_cloud_connect(payload: Annotated[dict, Body()]) -> dict:
        """Create a `[[mcp.servers]]` entry from a template.

        The token never comes back and never lands in `config.toml`: secrets are
        written to `mcp-secrets.toml` (0600) and merged into the child's
        environment at spawn. The file is then re-read to prove the entry parses -
        a panel that says "connected" on unparsable TOML is a lie.
        """
        from .. import mcp, mcp_cloud
        from ..providers_admin import is_loopback_bind

        body = payload or {}
        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail="connecting a cloud server is only allowed when uh web listens on loopback",
            )
        try:
            created = await asyncio.to_thread(
                mcp_cloud.connect,
                str(body.get("template") or ""),
                str(body.get("name") or ""),
                {str(k): str(v) for k, v in (body.get("fields") or {}).items()},
            )
        except mcp_cloud.CloudError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        servers, problems = await asyncio.to_thread(mcp.servers_from_config)
        known = any(server.name == created["name"] for server in servers)
        return {
            **created,
            "parsed": known,
            "problems": problems,
            "state": "connected" if known else "written-but-unreadable",
        }

    @app.post("/api/mcp/http", dependencies=[Depends(require_token)])
    async def mcp_http(payload: Annotated[dict, Body()]) -> dict:
        """Record an HTTP/SSE MCP server (Positron, an IDE, a hosted tool server).

        Nothing is started: UH's client is stdio-only today, and the answer says so
        instead of pretending the server is connected.
        """
        from .. import mcp_cloud
        from ..providers_admin import is_loopback_bind

        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail="adding a server is only allowed when uh web listens on loopback",
            )
        body = payload or {}
        try:
            created = await asyncio.to_thread(
                mcp_cloud.connect_http,
                str(body.get("name") or ""),
                str(body.get("url") or ""),
                label=str(body.get("label") or ""),
                token=str(body.get("token") or ""),
            )
        except mcp_cloud.CloudError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return created

    @app.post("/api/mcp/custom", dependencies=[Depends(require_token)])
    async def mcp_custom(payload: Annotated[dict, Body()]) -> dict:
        """Add any MCP server the user can spell: name, command, args, env.

        Same rules as a template: one `[[mcp.servers]]` block, credentials in
        `mcp-secrets.toml`, and the config is re-read before the panel is told the
        server exists.
        """
        from .. import mcp, mcp_cloud
        from ..providers_admin import is_loopback_bind

        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail="adding a server is only allowed when uh web listens on loopback",
            )
        body = payload or {}
        raw_args = body.get("args") or []
        if isinstance(raw_args, str):
            raw_args = raw_args.splitlines()
        env = body.get("env") or {}
        if not isinstance(env, dict):
            raise HTTPException(status_code=400, detail="env must be an object of KEY=value")
        try:
            created = await asyncio.to_thread(
                mcp_cloud.connect_custom,
                str(body.get("name") or ""),
                str(body.get("command") or ""),
                [str(item) for item in raw_args],
                {str(k): str(v) for k, v in env.items()},
            )
        except mcp_cloud.CloudError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        servers, problems = await asyncio.to_thread(mcp.servers_from_config)
        known = any(server.name == created["name"] for server in servers)
        return {
            **created,
            "parsed": known,
            "problems": problems,
            "state": "connected" if known else "written-but-unreadable",
        }

    @app.post("/api/mcp/{name}/test", dependencies=[Depends(require_token)])
    async def mcp_test(name: str) -> dict:
        """Start one configured MCP server, list its tools, stop it again.

        The button asks the server a question *now*: the answer is never served
        from the process cache, so a green dot here means this second.
        """
        from .. import mcp

        servers, _ = await asyncio.to_thread(mcp.servers_from_config)
        if not any(server.name == name for server in servers):
            raise HTTPException(status_code=404, detail=f"no MCP server named '{name}'")
        try:
            return await asyncio.to_thread(mcp.test_server, name)
        except mcp.MCPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

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


def _port_busy(host: str, port: int, timeout: float = 0.4) -> bool:
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def first_free_port(host: str, port: int, tries: int = 10) -> Optional[int]:
    """The first port that nothing answers on, starting at ``port``."""
    for candidate in range(port, port + tries):
        if not _port_busy(host, candidate):
            return candidate
    return None


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    app_window: bool = False,
    allow_terminal: bool = False,
    workspace: Optional[Path] = None,
    static_dir: Optional[Path] = None,
    port_fallback: bool = False,
) -> int:
    """Start uvicorn and print the tokenised URL. Returns the exit code."""
    import uvicorn

    # A busy port used to be uvicorn's exit code 3 and nothing else: the user saw
    # a popup "uh web stopped" with no cause. An old `uh web` still holding the
    # port is the common case, and the interface it serves is read from disk - so
    # the browser gets the new files and the server does not know the new routes
    # ("Not Found" on a button that exists). Move to the next free port instead.
    if _port_busy(host, port):
        free = first_free_port(host, port + 1) if port_fallback else None
        if free is None:
            where = f"{port}-{port + 9}" if port_fallback else str(port)
            print(
                f"uh web: {host}:{where} {'are all in use' if port_fallback else 'is already in use'}."
                " Stop the other `uh web` or pass --port.",
                file=sys.stderr,
            )
            return 1
        print(f"uh web: {host}:{port} is already in use (another `uh web`?); using {free}")
        port = free

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
        if app_window:
            print("  window: app mode (Edge/Chrome --app, no tab bar)")
            threading.Timer(1.0, _open_app_window, args=(url,)).start()
        else:
            threading.Timer(1.0, _open_browser, args=(url,)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _server_ready(url: str, timeout: float = 15.0) -> bool:
    """Wait until the server answers, so a window never opens on an error page.

    The launcher fires on a timer while uvicorn is still binding; on a cold start
    that is a tab (or an app window) showing "connection refused", which reads as
    a broken product. The probe asks the bare URL without the token: consuming the
    token here would leave the real window holding a used one.
    """
    import httpx

    base = url.split("?", 1)[0]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(base, timeout=1.0)
            return True  # any HTTP answer, 401 included, means uvicorn is up
        except Exception:  # noqa: BLE001 - not up yet is the normal case
            time.sleep(0.2)
    return False


def _open_app_window(url: str) -> None:
    """Chromeless window, or the honest fallback. Never raises."""
    from . import browser

    if not _server_ready(url):
        print(f"uh web: the server did not answer within 15s; open {url} yourself")
        return
    try:
        if browser.open_app_window(url):
            return
        print(f"uh web: {browser.fallback_reason()}")
    except Exception:  # noqa: BLE001 - a broken launcher must not kill the server
        pass
    _open_browser(url)


def _open_browser(url: str) -> None:
    import webbrowser

    if not _server_ready(url):
        print(f"uh web: the server did not answer within 15s; open {url} yourself")
        return
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box has no browser
        pass
