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
import base64
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Callable, Iterator, Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, attachments, turnlog
from .. import commands as uh_commands
from .. import config as uh_config
from .. import roles as uh_roles
from .. import skill_prefs as uh_skill_prefs
from ..console import ChatSession, Console
from ..providers import Provider, ProviderError
from ..store import Store, display_id
from ..toolsets import build_toolsets
from .sse import frame

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from ..toolsets.file import FileToolset

STATIC_DIR = Path(__file__).parent / "static"
#: The cookie is host-scoped, not port-scoped: with one name, two `uh web` on
#: 127.0.0.1 overwrite each other's token and the older tab answers 401 to
#: everything. One name per port gives each instance its own slot in the same jar.
COOKIE_PREFIX = "uh_token"
#: The token used to be random per process, so every restart invalidated every open
#: tab. It now lives in `$UH_HOME/web-token` (0600) and survives a restart;
#: `uh web --rotate-token` replaces it on purpose.
TOKEN_FILE = "web-token"
#: Requests that 404 are otherwise invisible once the server runs without a
#: terminal (the shortcut's hidden console swallows uvicorn's access log).
#: A missing asset then costs an F12 session on the user's side; this line puts
#: the path and its referer where the server is started from.
LOGGER = logging.getLogger("uh.web")
COOKIE_NAME = COOKIE_PREFIX  # kept for older imports; the app uses its port-scoped name
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
#: Methods that change something. A cookie is ambient authority: the browser
#: attaches it to any request a page makes, so these are refused when the only
#: credential is the cookie and the request does not prove it came from us.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
#: An import arrives as one body: the cap stops a wrong file from filling the
#: disk, and the route stops reading at the boundary instead of after it.
MAX_IMPORT_BYTES = 256 * 1024 * 1024
#: Life sign cadence on a chat stream, and the hard stop: a turn that never returns
#: used to leave `state.streaming` true for ever, and the next message vanished.
HEARTBEAT_S = 15.0
TURN_CAP_S = 600.0
#: Tools the panel's "Tester" button may run for a remote MCP server. Read-only by
#: contract, decided on the name family: a button is not a blank cheque - anything
#: that writes still goes through a chat turn and its approval card.
MCP_PROBE_TOOL_RE = re.compile(r"^(list_|get_|search_|read|list$|describe|info)", re.IGNORECASE)
MCP_PROBE_TOOLS_HELP = "list_*, get_*, search_*, read* (lecture seule)"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _local_tools_ok(name: str) -> Optional[bool]:
    """Whether a local model declares tool support (``None`` when it cannot say).

    Ollama answers ``/api/show``; older builds do not, and "unknown" is a fact the
    header can show - unlike a guess, which would label a working model as broken.
    """
    from ..providers.ollama import model_capabilities

    capabilities = model_capabilities(name)
    return None if capabilities is None else "tools" in capabilities


def answer_meta(session_obj: object, kind: str, model: str) -> "dict | None":
    """Which model actually answered, as a frame the browser can trust.

    ``last_real_model`` is what the provider says it served, which is not always the
    configured alias: ``auto/best-free`` resolves to a concrete name, and a fallback
    switches provider mid-flight. Point 5 asks the header, the answer's footer and
    the local-model tab to name that model - one value, emitted once, and ``None``
    when nothing has answered yet (a frame that says nothing is better than a frame
    that invents a model).
    """
    served = str(getattr(session_obj, "last_real_model", "") or "")
    if not served:
        return None
    requested = str(model or "")
    return {
        "type": "answer_meta",
        "provider": str(kind or ""),
        "model": served,
        "requested": requested,
        "substituted": bool(requested and requested != served),
    }


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


def _redact(text: str) -> str:
    """No stored secret value leaves through an error message."""
    try:
        from .. import mcp_cloud

        return mcp_cloud.redact(text)
    except Exception:  # noqa: BLE001 - redaction never fails a response
        return str(text)


def _bearer(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-uh-token")


def require_token(request: Request) -> None:
    cookie_name = getattr(request.app.state, "cookie_name", COOKIE_NAME)
    supplied = request.cookies.get(cookie_name) or _bearer(request)
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


_WORKSHOP_PROBE: list[float] = [0.0]


def _expose_workshop_when_uhb_is_in_dev(*, force: bool = False) -> bool:
    """Offer `execute_code` when UHB really is in the workshop mode, whatever launched UH.

    The desktop shortcut starts `uh web` without any environment variable, so the old
    rule - "the tool exists only if UH was launched with UH_UHB_EXECUTE=1" - left the
    cockpit with seven tools after the operator clicked Atelier. The mode is the fact
    that matters, and it is measured here, once per turn, on the same probe the
    toolset already pays for. Nothing is granted: UHB still refuses a call without the
    approval nonce the operator's click produces.
    """
    if os.environ.get("UH_UHB_EXECUTE") == "0":
        return False
    # Cached for a few seconds: the probe is paid once per turn at most, never twice,
    # and a dead UHB costs 0.6 s rather than a stall (a turn must not hang on a panel).
    # `force` exists for the test that must measure the probe itself, not the cache
    # another turn happened to fill (that order dependence kept one line red).
    now = time.monotonic()
    if not force and now - _WORKSHOP_PROBE[0] < 5.0:
        return os.environ.get("UH_UHB_EXECUTE") == "1"
    _WORKSHOP_PROBE[0] = now
    try:
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        url = resolve_url()
        if not url:
            return False
        status = Uhbtoolset(url, timeout=0.6).status()
    except Exception:  # noqa: BLE001 - a dead UHB must not break a text turn
        return False
    if str(status.get("mode") or "").strip() == "dev":
        os.environ["UH_UHB_EXECUTE"] = "1"
        return True
    return False


def _toolsets(
    app: FastAPI,
    *,
    workspace: Path | str | None,
    read_roots: list[Path] | None = None,
    web_search: bool = True,
    notify=None,
) -> dict:
    """Toolsets of ONE turn, built from that turn's own context.

    `workspace` and `read_roots` are required keyword arguments on purpose. They used to be
    carried by `app.state.turn_workspace` / `turn_read_roots`, which is shared by the whole
    process: two concurrent turns wrote the same attribute, and the first one could inherit
    the SECOND one's upload roots - measured, `tests/test_turn_isolation.py` failed with
    "A received the context of session-b". A turn's context now lives in the turn's own
    variables; nothing about a turn is stored on the application.
    """
    _expose_workshop_when_uhb_is_in_dev()
    # Les pieces jointes de la session sont lisibles (lecture seule) par l'outil `file` :
    # elles vivent hors workspace. `~/.uh` n'est jamais ouvert, seulement ce dossier.
    toolsets = build_toolsets(
        workspace or app.state.settings.workspace,
        notify=notify,
        read_roots=list(read_roots or []) or None,
    )
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


def _install_info() -> dict:
    """Which copy of UH this process is: a checkout, or the installed package.

    The whole class of "the fixes never reach me" came from this - the shortcut ran
    Python310's installed 1.6.1 while the work lived in a checkout - and nothing in
    the product said so.
    """
    import uh as uh_package

    package = Path(uh_package.__file__).resolve()
    root = package.parent.parent
    editable = "site-packages" not in str(package) and (root / "pyproject.toml").is_file()
    return {
        "package": str(package),
        "root": str(root),
        "editable": editable,
        "python": sys.executable,
        # What the operator cannot otherwise see, and what has cost whole sessions:
        # the commit the running code comes from, and whether that commit is dirty.
        "commit": _git_commit(root),
        "dirty": _git_dirty(root),
        "origin": "dépôt" if editable else "paquet installé",
    }


def _git_commit(root: Path) -> str:
    """Short SHA of the checkout this code lives in, or empty for an installed copy."""
    try:
        proc = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "--short", "HEAD"),
            capture_output=True, text=True, encoding="utf-8", timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_dirty(root: Path) -> bool:
    try:
        proc = subprocess.run(
            ("git", "-C", str(root), "status", "--porcelain"),
            capture_output=True, text=True, encoding="utf-8", timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(proc.stdout.strip())


def _read_head(path: Path, limit: int) -> bytes:
    """Les premiers octets d'un fichier : décider texte/binaire sans tout charger."""
    with path.open("rb") as handle:
        return handle.read(max(1, limit))


def _uh_version_on(host: str, port: int, timeout: float = 1.5) -> Optional[str]:
    """Version of the UH answering on this port, or ``None``.

    ``/healthz`` is unauthenticated on purpose: a second instance has no token for
    the first one, and the answer it needs ("is this port already a UH?") carries
    no secret.
    """
    import httpx

    try:
        response = httpx.get(f"http://{host}:{port}/healthz", timeout=timeout)
    except Exception:  # noqa: BLE001 - any failure means "not a UH we can see"
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("app") != "uh":
        return None
    return str(payload.get("version") or "?")


def _open_url(url: str, *, app_window: bool = False) -> None:
    """Open the cockpit in a browser window, best effort."""
    import webbrowser

    try:
        if app_window:
            import subprocess as _subprocess

            for candidate in ("msedge", "chrome"):
                try:
                    _subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                        [candidate, f"--app={url}"],
                        stdout=_subprocess.DEVNULL,
                        stderr=_subprocess.DEVNULL,
                    )
                    return
                except OSError:
                    continue
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - opening a browser never fails a start
        pass


def _stable_token(rotate: bool = False) -> str:
    """The web token for this UH home: created once, reused at every restart.

    A per-process token meant that any restart invalidated every open tab, which
    reads exactly like "the app broke": the console fills with 401 and the page
    used to be replaced by one sentence. The file is 0600, outside the repository,
    and the transport guards (loopback bind, Host/Origin, Sec-Fetch-Site) are what
    actually protect it - a rotating string was never the security boundary.
    """
    import stat

    path = uh_config.uh_home() / TOKEN_FILE
    if rotate or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="utf-8")
        try:
            # POSIX: owner read/write only. Windows has no such bits - chmod there
            # only toggles the read-only flag, and the file is protected by the
            # profile's ACL instead. Say that rather than promise 0600 everywhere.
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover - a filesystem without chmod
            pass
        return token
    saved = path.read_text(encoding="utf-8").strip()
    return saved or _stable_token(rotate=True)


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
    app.state.token = token or _stable_token(rotate=False)
    app.state.cookie_name = f"{COOKIE_PREFIX}_{port}"
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
            # `chain[0]` is the first link of the *fallback* order (local first),
            # not necessarily the pair the user chose. Reporting it made the header
            # say "ollama · deepseek-flash": two things that do not go together.
            preferred_kind = str(cfg.get("default_provider") or "").strip().lower()
            kinds = [kind for kind, _ in chain]
            if preferred_kind and preferred_kind in kinds:
                app.state.kind = preferred_kind
                app.state.model = resolved
            else:
                # The chosen provider is not usable (no key, not installed): say
                # which pair is actually serving, model included.
                app.state.kind = chain[0][0]
                app.state.model = str(getattr(chain[0][1], "model", "") or resolved)
        except ProviderError as exc:
            app.state.provider_error = str(exc)

    allowed_hosts = _allowed_hosts(host, port)
    allowed_origins = _allowed_origins(host, port)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Every refusal here is 403, not 400: the request is well formed, the
        # caller is simply not allowed — and the browser turns a 403 into "you
        # cannot do that from this page", which is the honest sentence (point 4).
        if request.headers.get("host", "") not in allowed_hosts:
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in allowed_origins:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        # ``Sec-Fetch-Site`` is set by the browser itself and cannot be forged by
        # a page: a cross-site request is refused even when it carries no Origin.
        site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if site == "cross-site":
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        if (
            request.method in UNSAFE_METHODS
            and request.cookies.get(getattr(app.state, "cookie_name", COOKIE_NAME))
            and not _bearer(request)
        ):
            # Cookie-only authentication on a state-changing call: a page the
            # user happens to have open could otherwise POST to /api/mcp/http and
            # register a server of its choosing (CSRF / DNS rebinding).
            same_origin = bool(origin and origin in allowed_origins) or site == "same-origin"
            if not same_origin:
                return JSONResponse(
                    {
                        "detail": (
                            "requête refusée : elle modifie l'état sans preuve d'origine. "
                            "Recharge la page UH et réessaie."
                        )
                    },
                    status_code=403,
                )
        response = await call_next(request)
        if response.status_code == 404:
            LOGGER.warning(
                "404 %s referer=%s",
                request.url.path,
                request.headers.get("referer") or "-",
            )
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
        # `?v=` doit changer quand le CODE change, pas quand la version change : deux
        # correctifs publies sous la meme version gardaient la meme URL, et un navigateur
        # pouvait servir l'ancien app.js - mesure le 19/11 (fenetre sur le paquet installe).
        build = _install_info().get("commit") or __version__
        page = page.replace("{{BUILD}}", str(build)).replace("{{VERSION}}", __version__)
        cookie = request.cookies.get(app.state.cookie_name)
        if token and secrets.compare_digest(token, app.state.token):
            response = HTMLResponse(page)
            response.set_cookie(
                app.state.cookie_name,
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

    # -- health ------------------------------------------------------------ #
    @app.get("/healthz")
    async def healthz() -> dict:
        """Unauthenticated, and deliberately boring: "a UH answers here, version X".

        It is what lets a second `uh web` recognise the one already holding the port,
        instead of silently serving a different copy on the next free port - the
        state where the browser has new files and the server does not know the new
        routes.
        """
        return {"app": "uh", "version": __version__}

    # -- icon fallbacks ---------------------------------------------------- #
    # Declared in index.html as data URIs, so a healthy page never asks for
    # these. Browsers, bookmark bars and "add to home screen" still hit them
    # directly: an empty 204 is the honest answer (there is no file to send),
    # and it keeps the console free of a 404 that no code change can fix.
    @app.get("/favicon.ico")
    @app.get("/apple-touch-icon.png")
    @app.get("/apple-touch-icon-precomposed.png")
    async def icon_fallback() -> Response:
        return Response(status_code=204)

    # -- status ------------------------------------------------------------ #
    @app.get("/api/status", dependencies=[Depends(require_token)])
    async def status(session_id: str = "") -> dict:
        with _store(app) as store:
            sessions = len(store.list_sessions(limit=10_000))
            schema = store.schema_version
        # Gate B found this: a session can run on a model of its own (`POST /api/model`
        # with a session_id), and the header kept announcing the global default -
        # exactly the "two models on screen" the interface rules forbid. Asking for a
        # session's status gives that session's pair.
        kind, model = app.state.kind, app.state.model
        if session_id:
            kind, model = app.state.session_models.get(session_id, (kind, model))
        return {
            "version": __version__,
            "provider": kind,
            "model": model,
            "provider_error": app.state.provider_error,
            "sessions": sessions,
            "store": str(app.state.settings.store_path),
            "schema_version": schema,
            "terminal": app.state.settings.allow_terminal,
            "workspace": str(app.state.settings.workspace),
            "install": _install_info(),
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
        if (
            "title" not in body
            and "folder" not in body
            and "settings" not in body
            and "role" not in body
        ):
            raise HTTPException(
                status_code=400,
                detail="nothing to update: send title, folder, settings or role",
            )
        with _store(app) as store:
            if "title" in body:
                title = str(body.get("title") or "")[:120]
                store.rename_session(session["id"], title)
            if "folder" in body:
                store.set_folder(session["id"], str(body.get("folder") or ""))
            if "settings" in body or "role" in body:
                # Re-read after the folder write: `set_folder` does its own
                # read-modify-write, and a stale copy here would drop it.
                current = store.get_session(session["id"]) or session
                metadata = dict(current.get("metadata") or {})
                if "settings" in body:
                    # Stored on the session, not in the browser: a setting that
                    # changes an answer belongs with the conversation it changed.
                    blob = body.get("settings") or {}
                    values = blob.get("values") if isinstance(blob, dict) else None
                    metadata["settings"] = {
                        "enabled": bool((blob or {}).get("enabled")),
                        "values": values if isinstance(values, dict) else {},
                    }
                if "role" in body:
                    # Character & role (P3-14): the posture, and nothing else. An
                    # unknown preset is refused by `normalise`, never silently kept.
                    role = uh_roles.normalise(body.get("role"))
                    if role is None:
                        metadata.pop("role", None)
                    else:
                        metadata["role"] = role
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
            "role": (updated.get("metadata") or {}).get("role"),
            "session": {**updated, "short_id": display_id(updated["id"])},
        }

    # -- folders ----------------------------------------------------------- #
    # A folder is a value in session metadata, so these two routes are bulk
    # updates of one key. The list route exists because a folder with no session
    # in it does not exist at all: the sidebar cannot invent one.
    # Character & role (P3-14): the presets the interface renders. The posture text
    # is not sent to the client — it is applied server-side when the role is chosen,
    # so a browser cannot rewrite the system prompt.
    @app.get("/api/roles", dependencies=[Depends(require_token)])
    async def list_roles() -> dict:
        return {"presets": uh_roles.presets_payload(), "none": uh_roles.NONE_LABEL}

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

    @app.post("/api/artifacts/action", dependencies=[Depends(require_token)])
    async def artifact_action(payload: dict) -> dict:
        """Keep, trash or rate one image, from its provenance.

        The page already holds the provenance (it arrives with the artifact), so
        the same call works for a live turn and for a reloaded one. What it may
        touch is decided here, not there: ``uh/artifacts.py`` refuses any path that
        does not resolve inside UHB's runs root, so a forged body can neither read
        nor delete another file. Refusals are a 400 with the sentence the page shows.
        """
        from .. import artifacts as uh_artifacts

        source = payload.get("source")
        provenance = source if isinstance(source, dict) else {}
        action = str(payload.get("action") or "").strip().lower()
        try:
            if action == "keep":
                result = uh_artifacts.keep(provenance)
                # P1-13: what was kept is copied into the memory, in a canonical
                # format (PNG lossless / FLAC 24 bits). A failure there must not
                # undo the keep that already succeeded: it is reported beside it.
                try:
                    from .. import memory as uh_memory

                    kind = str(payload.get("kind") or "").lower() or "image"
                    if kind not in {"image", "audio"}:
                        kind = "image"
                    result["memory"] = uh_memory.keep(
                        Path(str(result.get("file") or "")),
                        kind=kind,
                        session_id=str(payload.get("session_id") or ""),
                        message_id=payload.get("message_id"),
                    )
                except Exception as exc:  # noqa: BLE001 - the keep already happened
                    result["memory_error"] = f"{type(exc).__name__}: {exc}"
                return result
            if action == "trash":
                return uh_artifacts.trash(provenance)
            if action == "restore":
                return uh_artifacts.restore(provenance)
            if action == "rate":
                return uh_artifacts.rate(
                    provenance,
                    str(payload.get("vote") or ""),
                    session_id=str(payload.get("session_id") or ""),
                    message_id=payload.get("message_id"),
                    index=int(payload.get("index") or 0),
                    comment=str(payload.get("comment") or ""),
                )
        except uh_artifacts.ArtifactActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail="action must be keep, trash, restore or rate")

    @app.get("/api/artifacts/trash", dependencies=[Depends(require_token)])
    async def artifact_trash() -> dict:
        """How much waits in the trash, before the operator empties it."""
        from .. import artifacts as uh_artifacts

        return {"files": uh_artifacts.trash_count(), "root": str(uh_artifacts.runs_root())}

    @app.post("/api/artifacts/trash/empty", dependencies=[Depends(require_token)])
    async def artifact_trash_empty() -> dict:
        """Empty the trash. Only the trash: a run directory is never swept here.

        P1-13: emptying the bin is also what forgets the memory entries it holds. The
        undo window of « Corbeille » is why this is here and not in `trash`: an entry
        dropped at click time would die while the file was still recoverable.
        """
        from .. import artifacts as uh_artifacts
        from .. import memory as uh_memory

        folder = uh_artifacts.runs_root() / uh_artifacts.TRASH_DIR
        sources = []
        if folder.is_dir():
            for sidecar in folder.rglob("*.json"):
                try:
                    sources.append(str(json.loads(sidecar.read_text(encoding="utf-8")).get("from") or ""))
                except (OSError, ValueError):
                    continue
        result = uh_artifacts.empty_trash()
        forgotten = 0
        for source in sources:
            if source:
                forgotten += uh_memory.drop_for(Path(source))
        result["memory_forgotten"] = forgotten
        return result

    @app.get("/api/memory", dependencies=[Depends(require_token)])
    async def memory_entries() -> dict:
        """What the operator kept, in canonical format, and what it costs."""
        from .. import memory as uh_memory

        return {"entries": uh_memory.entries(), "weight": uh_memory.weight()}

    @app.get("/api/maintenance", dependencies=[Depends(require_token)])
    async def maintenance_summary(session_id: str = "") -> dict:
        """Orphans both ways for one session - shown, never silently deleted."""
        from .. import attachments as _att
        from .. import maintenance as uh_maintenance

        if not session_id:
            return {"ok": False, "detail": "session_id requis"}
        session = _session_or_404(app, session_id)
        with _store(app) as store:
            rows = store.get_messages(session["id"], limit=5000)
        folder = _att.session_dir(session["id"])
        swept = uh_maintenance.sweep_folder(folder)
        return {
            "ok": True,
            "swept": swept,
            "summary": uh_maintenance.summary(folder, rows),
        }

    # -- projects and the run lifecycle ------------------------------------ #
    @app.get("/api/projects", dependencies=[Depends(require_token)])
    async def projects_list(session_id: str = "") -> dict:
        """The current project first, then the registry, each with a measured Git state.

        ``porcelain`` is returned raw on purpose: Gate D compares it byte for byte with
        what a terminal prints, so anything derived here would be the wrong evidence.
        """
        from .. import projects as uh_projects

        current = app.state.settings.workspace
        if session_id:
            session = _session_or_404(app, session_id)
            current = (session.get("metadata") or {}).get("workspace") or current
        data = uh_projects.listing(current)
        return {"ok": True, **data}

    @app.post("/api/projects/add", dependencies=[Depends(require_token)])
    async def projects_add(payload: dict) -> dict:
        """Register a folder as a project. The only path check that counts is git's."""
        from .. import projects as uh_projects

        raw = str(payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="path requis")
        try:
            entry = uh_projects.add(raw, str(payload.get("label") or ""))
        except uh_projects.ProjectsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "project": entry}

    @app.post("/api/projects/switch", dependencies=[Depends(require_token)])
    async def projects_switch(payload: dict) -> dict:
        """Move a session to another project, without restarting anything (D-5).

        The workspace is **per session**: a switch never moves a turn that is already
        running, and an old session keeps answering where it started.
        """
        from .. import projects as uh_projects

        raw = str(payload.get("path") or "").strip()
        if not raw:
            raise HTTPException(status_code=400, detail="path requis")
        try:
            target = uh_projects.switch(raw)
        except uh_projects.ProjectsError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            session = _session_or_404(app, session_id)
            metadata = dict(session.get("metadata") or {})
            metadata["workspace"] = str(target)
            with _store(app) as store:
                store.set_session_metadata(session["id"], metadata)
        else:
            app.state.settings.workspace = target
        return {"ok": True, "workspace": str(target), "session_id": session_id}

    @app.get("/api/runs", dependencies=[Depends(require_token)])
    async def runs_status() -> dict:
        """Proxy of UHB ``GET /runs``: UH shows, UHB decides."""
        from .. import uhb_runs

        return uhb_runs.status()

    @app.post("/api/runs", dependencies=[Depends(require_token)])
    async def runs_action(payload: dict) -> dict:
        """Proxy of UHB ``POST /runs``. ``prune`` stays a dry run unless ``apply`` is true."""
        from .. import uhb_runs

        action = str(payload.get("action") or "").strip().lower()
        if action == "close":
            run_id = str(payload.get("run") or "").strip()
            if not run_id:
                raise HTTPException(status_code=400, detail="run requis")
            return uhb_runs.close(run_id, bool(payload.get("keep")), str(payload.get("summary") or ""))
        if action == "prune":
            return uhb_runs.prune(bool(payload.get("apply")))
        if action == "restore":
            revision = str(payload.get("revision") or "").strip()
            if not revision:
                raise HTTPException(status_code=400, detail="revision requise")
            return uhb_runs.restore(revision)
        raise HTTPException(status_code=400, detail="action must be close, prune or restore")

    # -- media and model capability ---------------------------------------- #
    # P1-14. Two questions the page needs answered before it sends anything: what can
    # this model read, and which configured model could read it instead.
    @app.get("/api/media/capabilities", dependencies=[Depends(require_token)])
    async def media_capabilities(provider: str = "", model: str = "", kind: str = "") -> dict:
        from .. import capabilities as uh_caps
        from .. import media as uh_media

        kind = str(kind or "").strip() or "image"
        answer: dict = {
            "kinds": {
                "image": sorted(uh_media.IMAGE_SUFFIXES),
                "video": sorted(uh_media.VIDEO_SUFFIXES),
                "audio": sorted(uh_media.AUDIO_SUFFIXES),
                "document": sorted(uh_media.DOC_SUFFIXES),
            },
            "budget_bytes": uh_media.IMAGE_BUDGET,
            "max_side": uh_media.MAX_SIDE,
            "ffmpeg": uh_media.ffmpeg_path(),
        }
        if provider or model:
            active_provider = provider or app.state.kind
            active_model = model or app.state.model
            answer["model"] = uh_caps.can_read(active_provider, active_model, kind)
            answer["vision_candidates"] = uh_caps.vision_candidates()[:24]
        return answer

    @app.get("/api/models/vision", dependencies=[Depends(require_token)])
    async def vision_models() -> dict:
        """Configured models that can read an image - the one-click switch menu."""
        from .. import capabilities as uh_caps

        candidates = uh_caps.vision_candidates()
        return {"candidates": candidates[:24], "total": len(candidates)}

    # -- files / explorateur (P3-16c lot 1) -------------------------------- #
    # Lecture seule, et une seule porte : `FileToolset.resolve_read` décide. Une route qui
    # aurait sa propre logique de chemins finirait par diverger de la sandbox des outils -
    # c'est précisément le défaut que la revue de sécurité a fermé sur `~/.uh`. Tout ce qui
    # est refusé par les outils est donc refusé ici, avec le même 403 et le même texte.
    fs_text_bytes = 200_000
    fs_text_max = 1_000_000
    fs_image_max = 8 * 1024 * 1024
    fs_list_max = 1_000

    def _fs_tool(app) -> "FileToolset":
        from ..toolsets import protected_roots as uh_protected_roots
        from ..toolsets.file import FileToolset

        return FileToolset(
            workspace=app.state.settings.workspace,
            protected_roots=uh_protected_roots(),
        )

    def _fs_target(app, raw: str) -> Path:
        """Le chemin demandé, ou l'exception qui dit pourquoi il est refusé."""
        text = str(raw or "").strip()
        if not text:
            # L'explorateur s'ouvre sur le workspace : c'est la seule racine garantie.
            text = str(_fs_workspace(app))
        tool = _fs_tool(app)
        target, error = tool.resolve_read(text)
        if target is None or error:
            message = str((error or {}).get("error") or "refused")
            # `_resolve` préfixe ses refus par "refused" ; le reste est un usage invalide
            # (chemin vide) et non une tentative d'évasion.
            refused = message.startswith("refused")
            raise HTTPException(status_code=403 if refused else 400, detail=message)
        return target

    def _fs_workspace(app) -> Path:
        return Path(app.state.settings.workspace).expanduser().resolve()

    @app.get("/api/fs/list", dependencies=[Depends(require_token)])
    async def fs_list(path: str = "", limit: int = 400) -> dict:
        """Le contenu d'un dossier autorisé. Aucun octet de fichier ici, seulement des noms."""
        target = _fs_target(app, path)
        if not target.is_dir():
            raise HTTPException(status_code=400, detail=f"pas un dossier : {target}")
        try:
            children = list(target.iterdir())
        except OSError as exc:
            raise HTTPException(status_code=403, detail=f"illisible : {exc}") from exc
        entries: list[dict] = []
        for child in children:
            try:
                info = child.stat()
            except OSError:
                continue  # un lien mort ou un fichier verrouillé n'empêche pas le listing
            is_dir = child.is_dir()
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "kind": "dir" if is_dir else "file",
                    "size": 0 if is_dir else info.st_size,
                    "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(info.st_mtime)),
                    "ext": child.suffix.lower(),
                }
            )
        entries.sort(key=lambda item: (item["kind"] != "dir", item["name"].lower()))
        cap = max(1, min(int(limit or 400), fs_list_max))
        workspace = _fs_workspace(app)
        parent = ""
        chain: list[dict] = [{"name": "workspace", "path": str(workspace)}]
        try:
            relative = target.relative_to(workspace)
        except ValueError:
            # Une racine de lecture (uploads) n'est pas sous le workspace : on n'invente
            # pas de fil d'Ariane qui remonterait par un parent interdit.
            relative = None
        if relative is not None:
            walked = workspace
            for part in relative.parts:
                walked = walked / part
                chain.append({"name": part, "path": str(walked)})
        if target != workspace:
            # On ne propose de remonter que dans le workspace : ailleurs, le parent est
            # justement ce que les outils refusent.
            if target.parent == workspace or target.parent.is_relative_to(workspace):
                parent = str(target.parent)
        return {
            "path": str(target),
            "name": target.name,
            "workspace": str(workspace),
            "parent": parent,
            "chain": chain,
            "entries": entries[:cap],
            "total": len(entries),
            "truncated": len(entries) > cap,
        }

    @app.get("/api/fs/stat", dependencies=[Depends(require_token)])
    async def fs_stat(path: str = "") -> dict:
        target = _fs_target(app, path)
        try:
            info = target.stat()
        except OSError as exc:
            raise HTTPException(status_code=404, detail=f"introuvable : {exc}") from exc
        is_dir = target.is_dir()
        return {
            "path": str(target),
            "name": target.name,
            "kind": "dir" if is_dir else "file",
            "size": 0 if is_dir else info.st_size,
            "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(info.st_mtime)),
            "ext": target.suffix.lower(),
            "symlink": target.is_symlink(),
        }

    @app.get("/api/fs/preview", dependencies=[Depends(require_token)])
    async def fs_preview(path: str = "", max_bytes: int = 0) -> dict:
        """Un aperçu borné : du texte tronqué, une image en data URL, un PDF lu si possible."""
        target = _fs_target(app, path)
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"pas un fichier : {target}")
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise HTTPException(status_code=404, detail=f"introuvable : {exc}") from exc
        ext = target.suffix.lower()

        from .. import media as uh_media

        if ext in uh_media.IMAGE_SUFFIXES:
            if size > fs_image_max:
                raise HTTPException(
                    status_code=413,
                    detail=f"image trop lourde pour l'aperçu ({size} o, maximum {fs_image_max} o)",
                )
            mimes = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".svg": "image/svg+xml",
            }
            data = await asyncio.to_thread(target.read_bytes)
            return {
                "kind": "image",
                "name": target.name,
                "bytes": size,
                "data_url": f"data:{mimes.get(ext, 'application/octet-stream')};base64,"
                + base64.b64encode(data).decode("ascii"),
            }

        if ext == ".pdf":
            tool = _fs_tool(app)
            answer = await asyncio.to_thread(tool.read_pdf, str(target), "1-5")
            if answer.get("error"):
                # pypdf absent, ou PDF protégé : on le dit, on ne prétend pas avoir lu.
                return {"kind": "pdf", "name": target.name, "bytes": size, "readable": False,
                        "reason": str(answer["error"])}
            return {"kind": "pdf", "name": target.name, "bytes": size, "readable": True,
                    "text": str(answer.get("text") or ""), "truncated": bool(answer.get("truncated"))}

        cap = max(1, min(int(max_bytes or fs_text_bytes), fs_text_max))
        head = await asyncio.to_thread(_read_head, target, cap)
        if b"\x00" in head:
            return {"kind": "binary", "name": target.name, "bytes": size,
                    "reason": "aperçu non pris en charge pour ce format binaire"}
        return {
            "kind": "text",
            "name": target.name,
            "bytes": size,
            "text": head.decode("utf-8", "replace"),
            "truncated": size > len(head),
        }

    @app.get("/api/fs/raw", dependencies=[Depends(require_token)])
    async def fs_raw(path: str = "") -> FileResponse:
        """Les octets bruts, pour « Attacher au tour » : ils repartent vers l'upload existant.

        Toujours servi en `application/octet-stream` et en pièce jointe : un SVG ouvert
        directement dans l'onglet serait du script servi depuis notre propre origine.
        """
        target = _fs_target(app, path)
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"pas un fichier : {target}")
        size = target.stat().st_size
        if size > attachments.MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"fichier trop lourd : la limite est de "
                    f"{attachments.human_size(attachments.MAX_BYTES)} par pièce jointe."
                ),
            )
        return FileResponse(
            str(target), media_type="application/octet-stream", filename=target.name
        )

    # -- audio ------------------------------------------------------------- #
    # P1-15. Every audio path is its own route, and the package they use is
    # imported inside the handler: UH starts, chats and runs tools with no audio
    # engine at all, and a broken one cannot reach the text turn.
    @app.get("/api/audio/capabilities", dependencies=[Depends(require_token)])
    async def audio_capabilities() -> dict:
        """Which engines can run here - and the sentence explaining the ones that cannot."""
        from .. import audio as uh_audio

        return uh_audio.capabilities()

    @app.post("/api/audio/transcribe", dependencies=[Depends(require_token)])
    async def audio_transcribe(
        request: Request, session_id: str = "", name: str = "clip.wav", language: str = "fr"
    ) -> dict:
        """A recorded clip in, plain text out: what the composer inserts.

        The body is the audio itself (raw bytes), not multipart: the page has a Blob,
        and this keeps `python-multipart` out of the dependencies. The answer is text
        only - the caller turns it into an ordinary user message, which is the whole
        anti-regression rule of this lot.
        """
        from .. import audio as uh_audio

        data = await request.body()
        try:
            result = uh_audio.transcribe(
                data, filename=name, language=language, session_id=session_id or "audio"
            )
        except uh_audio.AudioUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return result

    @app.post("/api/audio/speak", dependencies=[Depends(require_token)])
    async def audio_speak(payload: dict) -> dict:
        """Text in, one WAV file out, with the reference the thread keeps instead of bytes."""
        from .. import audio as uh_audio

        body = payload or {}
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        reference = str(body.get("reference") or "").strip()
        reference_wav = None
        if reference:
            try:
                reference_wav = uh_audio.resolve_upload(reference)
            except uh_audio.AudioUnavailable as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            clip = uh_audio.speak(
                text,
                voice=str(body.get("voice") or "") or None,
                session_id=str(body.get("session_id") or "audio"),
                reference_wav=reference_wav,
            )
        except uh_audio.AudioUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return clip

    @app.get("/api/audio/file", dependencies=[Depends(require_token)])
    async def audio_file(path: str = "") -> FileResponse:
        """One stored clip, and only inside the uploads root."""
        from .. import audio as uh_audio

        try:
            target = uh_audio.resolve_upload(path)
        except uh_audio.AudioUnavailable as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if target.suffix.lower() not in uh_audio.AUDIO_SUFFIXES:
            raise HTTPException(status_code=400, detail="not an audio file")
        return FileResponse(str(target), media_type="audio/wav", filename=target.name)

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
                detail=(
                    f"fichier trop lourd : la limite est de "
                    f"{attachments.human_size(attachments.MAX_BYTES)} par pièce jointe. "
                    "Compressez-le, découpez-le, ou joignez seulement la partie utile."
                ),
            )
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > attachments.MAX_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "fichier trop lourd : la limite est de "
                        f"{attachments.human_size(attachments.MAX_BYTES)} par pièce jointe. "
                        "Compressez-le, découpez-le, ou joignez seulement la partie utile."
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
        # P1-14: files and no words is a question. The turn asks it in plain French,
        # as a normal user message, instead of sending a wall of file names and
        # hoping the model guesses what was wanted. Decided BEFORE composing: the
        # attachments block alone would already look like a message.
        if not text.strip() and raw_paths:
            text = attachments.AUTO_ANALYSIS
        try:
            # Inlined under 4 KB, referenced above it: the turn is pruned here,
            # before the history grows by a file.
            text = attachments.compose(text, raw_paths, session["id"])
        except attachments.AttachmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        turn_images: list[str] = []
        media_kinds: list[str] = []
        media_omitted = 0
        if raw_paths:
            prepared = attachments.media_for(raw_paths, session["id"])
            turn_images = list(prepared.get("images") or [])
            media_kinds = [str(item.get("kind") or "?") for item in prepared.get("items") or []]
            media_omitted = int(prepared.get("omitted") or 0)
            if media_omitted:
                # Said, not hidden: the model received a sample, and the operator can
                # see it in the message it was given.
                text = (
                    f"{text}\n\n[lot de {len(turn_images) + media_omitted} images : "
                    f"{len(turn_images)} envoyées au modèle, {media_omitted} non envoyées]"
                )
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
        loop = asyncio.get_running_loop()
        queue: "asyncio.Queue[dict]" = asyncio.Queue()

        # One journal line per turn event: what ran, how long, and how it ended. The
        # turn id ties the lines together; nothing a user typed goes in it.
        turn_id = uuid.uuid4().hex[:8]
        turn_started = time.perf_counter()
        # P1-14: what the turn actually sent, so "the image was attached" is a line
        # in the journal and not a memory of the browser.
        turnlog.event(
            "turn_media", turn=turn_id, images=len(turn_images), kinds=media_kinds
        )
        tool_since: dict[str, float] = {}
        turn_reason = "done"
        # The workspace this turn works in: the session's own if it switched project
        # (D-5), the server's default otherwise.
        _session_workspace = (session.get("metadata") or {}).get("workspace") or ""
        _workspace = Path(str(_session_workspace)) if _session_workspace else None
        # P3-16b: the runs of this turn, so they can be closed when it ends. UHB owns
        # the decision; this list only carries the gesture.
        turn_runs: list[dict] = []

        def emit(event: dict) -> None:
            nonlocal turn_reason
            kind = str(event.get("type") or "")
            try:
                if kind == "tool_call":
                    name = str(event.get("name") or "tool")
                    tool_since[name] = time.perf_counter()
                    turnlog.event("tool_start", turn=turn_id, tool=name)
                elif kind == "tool_result":
                    name = str(event.get("name") or "tool")
                    began = tool_since.pop(name, None)
                    elapsed = int((time.perf_counter() - began) * 1000) if began else None
                    # ok/error only: the payload is the user's data.
                    status = "error" if '"error"' in str(event.get("content") or "") else "ok"
                    turnlog.event("tool_end", turn=turn_id, tool=name, ms=elapsed, status=status)
                    # P3-16b: remember the run this tool produced, if any. The payload is
                    # UHB's own `ExecutionResult.to_dict`; a shape we do not recognise is
                    # simply not collected - it must never fail a turn.
                    if name == "execute_code":
                        try:
                            payload = json.loads(str(event.get("content") or "") or "{}")
                        except ValueError:
                            payload = {}
                        run_id = str(payload.get("run") or payload.get("run_dir") or "").strip()
                        if run_id:
                            name_only = Path(run_id).name if "\\" in run_id or "/" in run_id else run_id
                            turn_runs.append({"run": name_only, "keep": bool(payload.get("keep"))})
                    # A refusal caused by the mode gets its own event: the page turns
                    # it into one card whose button grants execution and re-sends the
                    # turn. Without it the user is told "switch to dev" and has to
                    # find a control - the exact dead end reported on 19/11.
                    from .. import mode_gate as uh_mode_gate

                    refused = uh_mode_gate.mode_refused_frame(name, event.get("content"))
                    if refused is not None:
                        loop.call_soon_threadsafe(queue.put_nowait, refused)
                elif kind == "error":
                    turn_reason = "error"
                    turnlog.event("turn_error", turn=turn_id, detail=str(event.get("message") or "")[:200])
            except Exception:  # noqa: BLE001 - the journal never breaks a turn
                pass
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                # The stream is gone (client left, or the cap fired): the worker still
                # finishes its tool call, and nobody is reading. Dropping the event is
                # the only correct move - re-raising would kill the worker thread.
                pass

        turnlog.event(
            "turn_start",
            turn=turn_id,
            session=display_id(session["id"]),
            provider=kind,
            model=model,
            budget=chat_params.get("max_tokens") if isinstance(chat_params, dict) else None,
        )

        def work() -> None:
            store = None
            session_obj = None
            # Built here, not before: an HTTP/SSE toolset needs `emit` to ask its
            # per-call approval on this turn's stream.
            # Le contexte du tour reste dans ce fil : rien n'est ecrit sur `app.state`.
            toolsets = _toolsets(
                app,
                workspace=_workspace,
                read_roots=[attachments.session_dir(session["id"])],
                web_search=search_on,
                notify=emit,
            )
            try:
                from .. import mcp_http as uh_mcp_http

                # Read the config from disk, not from `app.state.cfg`: a server
                # added from the panel is written *while the app runs*, and a
                # snapshot taken at import would hide it until a restart.
                toolsets.update(uh_mcp_http.build_toolsets(notify=emit))
            except Exception:  # noqa: BLE001 - a remote server never breaks a turn
                pass
            # Point 2: the mode decides what may be *offered*. UH reads it from the
            # UHB toolset (one cached /status); an unreachable UHB means an unknown
            # mode, and an unknown mode never narrows anything by guesswork.
            mode = ""
            current = getattr(toolsets.get("uhb"), "current_mode", None)
            if callable(current):
                try:
                    mode = str(current() or "")
                except Exception:  # noqa: BLE001 - a mode is never worth a turn
                    mode = ""
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
                    role=(session.get("metadata") or {}).get("role"),
                    console=Console(stream=io.StringIO(), err=io.StringIO()),
                    extra_system=skill_block,
                    params=chat_params,
                    mode=mode,
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
                        # A slash command is not the model: prefixing its failure
                        # with "the model failed" (what the browser did before)
                        # sent people looking at their provider key while the
                        # answer was a dead search endpoint.
                        emit({"type": "error", "message": result.err, "source": "command"})
                    else:
                        emit({"type": "token", "text": body})
                        emit({"type": "answer", "content": body})
                else:
                    session_obj.run_text(text, images=turn_images or None)
            except Exception as exc:  # noqa: BLE001 - reported to the browser
                emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                app.state.active.pop(session["id"], None)
                # What the model was actually offered, and what the mode withheld:
                # one line per turn, so "the gate is real" is a measurement in the
                # log rather than a sentence in a prompt (point 2).
                turnlog.event(
                    "turn_offer",
                    turn=turn_id,
                    mode=mode or "unknown",
                    offered=list(getattr(session_obj, "last_offered", []) or []),
                    withheld=list(getattr(session_obj, "last_withheld", []) or []),
                )
                turnlog.event(
                    "turn_end",
                    turn=turn_id,
                    reason=turn_reason,
                    ms=int((time.perf_counter() - turn_started) * 1000),
                    tools=len(tool_since),
                    # Point 3: how many times the turn re-asked a model that pasted
                    # code instead of calling the tool it was offered.
                    nudges=int(getattr(session_obj, "last_nudges", 0) or 0),
                )
                # Which model actually answered (point 5). The configured alias is
                # not always what served - decision.py records the substitution, and
                # this is the same fact carried to the browser, so the header, the
                # answer's footer and the local-model tab all name one model.
                meta = answer_meta(session_obj, kind, model)
                if meta:
                    emit(meta)
                emit({"type": "__end__"})
                # P1-14: the prepared copies lived for exactly one turn. What the
                # operator marked « Garder » survives, because the purge reads the
                # sidecar the media pipeline wrote next to each copy.
                try:
                    from .. import attachments as _att
                    from .. import media as uh_media

                    purged = uh_media.purge(_att.session_dir(session["id"]))
                    if purged:
                        turnlog.event("turn_purge", turn=turn_id, removed=purged)
                except Exception:  # noqa: BLE001 - a purge must never fail a turn
                    pass
                # P3-16b: close the runs this turn opened. UHB decides what "closed"
                # means (archive if kept, delete otherwise); UH only says the turn is
                # over. Wrapped for the same reason the purge is: bookkeeping must not
                # cost an answer that has already been given.
                if turn_runs:
                    try:
                        from .. import uhb_runs

                        closed = uhb_runs.close_many(turn_runs)
                        turnlog.event(
                            "turn_runs_closed",
                            turn=turn_id,
                            closed=len(closed),
                            ok=sum(1 for item in closed if item.get("ok")),
                        )
                    except Exception:  # noqa: BLE001
                        pass
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
            # No event for 15 s means "still working", not "no answer": the browser
            # needs a life sign, and so does the user watching a silent stream.
            waited = 0.0
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    waited += HEARTBEAT_S
                    if waited >= TURN_CAP_S:
                        turnlog.event("turn_end", turn=turn_id, reason="deadline")
                        yield frame(
                            "error",
                            {
                                "message": (
                                    "aucune réponse du modèle depuis "
                                    f"{int(TURN_CAP_S)} s : tour interrompu côté client"
                                )
                            },
                        )
                        yield frame("done", {"session": display_id(session["id"]), "capped": True})
                        return
                    yield frame("ping", {"waited": int(waited)})
                    continue
                waited = 0.0
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
                # `owned`: this UH process started the running UHB, so it may
                # restart it to change the mode. A process started elsewhere is
                # reported, never killed.
                "owned": uhb_spawn.running() is not None,
            }
        return {"connected": True, **payload, "owned": uhb_spawn.running() is not None}

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

    @app.post("/api/uhb/mode", dependencies=[Depends(require_token)])
    async def uhb_set_mode(payload: dict) -> dict:
        """Change UHB's execution mode, and say plainly when that is impossible.

        The mode is a property of the UHB PROCESS (``--mode``), not a per-request
        setting: there is no route on the server to change it while it runs. So
        switching means restarting it - and UH only restarts a process it owns.
        A UHB started by hand (or by a previous UH run) is reported, never killed:
        "just switch to dev" used to lead the operator to a panel with no control,
        and then to a sentence blaming the model.
        """
        from .. import uhb_spawn
        from ..providers_admin import is_loopback_bind
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        # Switching the mode is what turns code execution on: a UH reachable from
        # another machine must not offer it, whatever the token says (point 4).
        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail=(
                    "changer de mode n'est permis que si UH écoute en loopback : "
                    "un poste distant ne doit pas pouvoir ouvrir l'exécution de code."
                ),
            )

        wanted = str((payload or {}).get("mode") or "").strip().lower()
        if wanted not in ("chat", "dev", "review", "observe"):
            raise HTTPException(
                status_code=400, detail="mode must be chat, dev, review or observe"
            )
        url = resolve_url()
        if url is None:
            raise HTTPException(
                status_code=400, detail="UHB is disabled: UH_UHB_URL is empty or not loopback"
            )
        current = await asyncio.to_thread(lambda: Uhbtoolset(url, timeout=1.5).status())
        owned = uhb_spawn.running() is not None
        if str(current.get("mode") or "") == wanted:
            return {"mode": wanted, "changed": False, "owned": owned}
        if not owned:
            raise HTTPException(
                status_code=409,
                detail=(
                    "UHB a été démarré hors de UH (à la main, par un lancement "
                    "précédent, ou par une tâche planifiée) : UH ne tue pas un "
                    "processus qu'il ne possède pas. Arrête-le, puis démarre-le "
                    "depuis ce panneau."
                ),
            )
        await asyncio.to_thread(uhb_spawn.stop_running)
        try:
            await asyncio.to_thread(lambda: uhb_spawn.ensure(timeout=25.0, mode=wanted))
        except uhb_spawn.SpawnError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        after = await asyncio.to_thread(lambda: Uhbtoolset(url, timeout=2.0).status())
        if after.get("error"):
            return {
                "connected": False,
                "mode": wanted,
                "reason": str(after.get("error") or "unreachable"),
                "changed": True,
            }
        # The switch is the operator's gesture, so it is also what exposes the tool:
        # before this, `execute_code` only appeared if UH had been LAUNCHED with
        # UH_UHB_EXECUTE=1, which the desktop shortcut never set - clicking Atelier
        # changed UHB's mode and left the chat with seven tools and no workshop, which
        # is exactly what the cockpit showed. The per-call approval in UHB stays the
        # real gate: a tool that is offered still cannot run without the operator's
        # click on the approval card.
        os.environ["UH_UHB_EXECUTE"] = "1" if wanted == "dev" else "0"
        return {"connected": True, "changed": True, "execute_exposed": wanted == "dev", **after}

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
        """Every installed skill, each marked ``enabled`` — disabled ones included.

        Hiding a disabled skill would make it unreachable from the interface, so the
        operator could never turn it back on.
        """
        from .. import skills as uh_skills

        listing = uh_skills.list_skills()
        listing["skills"] = uh_skill_prefs.for_listing(listing.get("skills") or [])
        listing["disabled"] = uh_skill_prefs.disabled()
        return listing

    @app.get("/api/skills/{name}", dependencies=[Depends(require_token)])
    async def skills_read(name: str) -> dict:
        """One skill's full text, for the editor. A native skill is readable, not writable."""
        from .. import skills as uh_skills

        entry = next(
            (item for item in uh_skills.list_skills().get("skills") or []
             if str(item.get("name")) == str(name)),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown skill: {name}")
        full = uh_skills.read_skill(str(entry["name"])) or {}
        return {
            "name": entry["name"],
            "title": entry.get("title") or "",
            "description": entry.get("description") or "",
            "instructions": full.get("instructions") or "",
            "origin": entry.get("origin") or "user",
            "editable": str(entry.get("origin")) != "native",
        }

    @app.put("/api/skills/{name}", dependencies=[Depends(require_token)])
    async def skills_update(name: str, payload: dict) -> dict:
        """Rewrite a user skill. Native skills are refused (403), not silently ignored."""
        from .. import skills as uh_skills

        entry = next(
            (item for item in uh_skills.list_skills().get("skills") or []
             if str(item.get("name")) == str(name)),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown skill: {name}")
        if str(entry.get("origin")) == "native":
            raise HTTPException(status_code=403, detail="a native skill is never modified")
        body = payload or {}
        try:
            return uh_skills.update(
                name=str(entry["name"]),
                title=str(body.get("title") or ""),
                description=str(body.get("description") or ""),
                instructions=str(body.get("instructions") or ""),
            )
        except uh_skills.SkillError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/skills/{name}/duplicate", dependencies=[Depends(require_token)])
    async def skills_duplicate(name: str, payload: dict | None = None) -> dict:
        from .. import skills as uh_skills

        body = payload or {}
        try:
            return uh_skills.duplicate(
                str(name), new_name=(str(body["name"]) if body.get("name") else None)
            )
        except uh_skills.SkillError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/skills/{name}", dependencies=[Depends(require_token)])
    async def skills_delete(name: str) -> dict:
        """Delete a user skill. A native skill has no file here, so it cannot be deleted."""
        from .. import skills as uh_skills

        entry = next(
            (item for item in uh_skills.list_skills().get("skills") or []
             if str(item.get("name")) == str(name)),
            None,
        )
        if entry is not None and str(entry.get("origin")) == "native":
            raise HTTPException(status_code=403, detail="a native skill is never deleted")
        try:
            return uh_skills.delete(str(name))
        except uh_skills.SkillError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/skills/import", dependencies=[Depends(require_token)])
    async def skills_import(payload: dict) -> dict:
        """Import a Markdown skill: the CLIENT sends content, never a path.

        Validation is the same one creation uses (name, title, instructions, bounds),
        plus a duplicate check. Nothing is executed: the file is text, and the skill
        only becomes part of a prompt when a turn selects it.
        """
        from .. import skills as uh_skills

        body = payload or {}
        filename = str(body.get("filename") or "").strip()
        content = str(body.get("content") or "")
        if not filename or not content.strip():
            raise HTTPException(status_code=400, detail="send {filename, content}")
        stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if stem.lower().endswith(".md"):
            stem = stem[:-3]
        if (uh_skills.user_dir() / f"{stem.lower()}.md").exists():
            raise HTTPException(status_code=409, detail=f"{stem} already exists")
        try:
            parsed = uh_skills.parse(content)
            return uh_skills.create(
                name=stem,
                title=str(parsed.get("title") or ""),
                description=str(parsed.get("description") or ""),
                instructions=str(parsed.get("instructions") or ""),
            )
        except uh_skills.SkillError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/skills/{name}/enabled", dependencies=[Depends(require_token)])
    async def skills_set_enabled(name: str, payload: dict) -> dict:
        """Turn one skill on or off. The name must exist in the catalogue.

        The id is ``origin:name`` computed by the server from the real listing: a
        client cannot address a file that is not a skill, and no path ever crosses
        this route.
        """
        from .. import skills as uh_skills

        body = payload or {}
        if "enabled" not in body or not isinstance(body.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="send {enabled: true|false}")
        entry = next(
            (item for item in uh_skills.list_skills().get("skills") or []
             if str(item.get("name")) == str(name)),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown skill: {name}")
        try:
            return uh_skill_prefs.set_enabled(
                str(entry["name"]), str(entry.get("origin") or "user"), bool(body["enabled"])
            )
        except uh_skill_prefs.SkillPrefsError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

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

    @app.get("/api/tools/catalog", dependencies=[Depends(require_token)])
    async def tools_catalog(mode: str = "", name: str = "") -> dict:
        """The tool catalogue, read from UHB and passed through untouched.

        No copy lives here: UH asks UHB, and if UHB is down the panel gets a
        structured *state* - ``{"available": false, "reason": ...}`` - not a 500 and
        not an empty list, which would read as "there are no tools".
        """
        from ..toolsets.uhb import Uhbtoolset, resolve_url

        url = resolve_url()
        if url is None:
            return {
                "available": False,
                "reason": {"code": "uhb_disabled", "hint": "UH_UHB_URL is empty or not loopback"},
                "tools": [],
            }
        payload = await asyncio.to_thread(
            lambda: Uhbtoolset(url, timeout=2.0).describe_tools(name, mode)
        )
        if "error" in payload:
            return {
                "available": False,
                "reason": {
                    "code": "uhb_unreachable",
                    "hint": str(payload.get("error") or "UHB did not answer"),
                },
                "tools": [],
            }
        return {"available": True, **payload}

    @app.get("/api/tools/catalog/{name}", dependencies=[Depends(require_token)])
    async def tools_catalog_one(name: str, mode: str = "") -> dict:
        return await tools_catalog(mode=mode, name=name)

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
        # HTTP/SSE entries are not started by the stdio client, but they are
        # configured and they have a live state of their own (P14.1). Counting
        # only the stdio servers made the panel say "nothing configured" over a
        # Positron entry that exists.
        from .. import mcp_http as uh_mcp_http

        # One handshake attempt per unprobed entry, so the panel can say
        # "connecté, 9 outils" instead of "configuré, 0 outil" over a server that
        # nothing had ever tried. Bounded: a server that hangs must not hang the
        # panel, and the bounded fallback is the network-free snapshot.
        def _probe() -> list:
            return uh_mcp_http.status(probe=True)

        try:
            http_rows = await asyncio.wait_for(asyncio.to_thread(_probe), timeout=8.0)
        except asyncio.TimeoutError:
            http_rows = await asyncio.to_thread(uh_mcp_http.status)
        http_rows = [
            {**row, "error": row.get("error") or row.get("reason") or ""} for row in http_rows
        ]
        if http_rows:
            connected_count = sum(1 for row in http_rows if row.get("connected"))
            summary = f"{connected_count}/{len(http_rows)} serveur(s) HTTP connecté(s)"
            mcp_status = {
                **mcp_status,
                "configured": int(mcp_status.get("configured") or 0) + len(http_rows),
                "http": http_rows,
                "http_reason": summary,
                # The stdio half may be empty; its "nothing configured" sentence
                # would then contradict the rows right beside it.
                "reason": summary if not mcp_status.get("servers") else mcp_status.get("reason", ""),
            }
        elif not mcp_status.get("configured"):
            from .. import mcp_cloud

            pending = await asyncio.to_thread(mcp_cloud.pending_http_servers)
            if pending:
                mcp_status = {
                    **mcp_status,
                    "configured": len(pending),
                    "reason": (
                        f"{len(pending)} serveur(s) HTTP/SSE configuré(s) : ouvre "
                        "« Connexions » et clique sur « Tester la connexion »."
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
        """Record one choice: a provider, optionally a key, optionally a model.

        Validated before it is written: an uninstalled local model or an id the
        provider does not publish used to be saved happily, then every turn failed
        with a bare ``HTTP 500``. A refused write is a 409 with the path, never an
        unhandled exception.
        """
        from ..config import write_layer
        from ..providers_admin import add_key_to, catalog

        body = payload or {}
        choice = str(body.get("choice") or "").strip().lower()
        model = str(body.get("model") or "").strip()
        key = str(body.get("key") or "").strip()
        if choice == "ollama":
            provider = "ollama"
            if model:
                installed: list[str] = []
                try:
                    from ..providers.ollama import list_models

                    installed = [entry["name"] for entry in list_models(timeout=2.0)]
                except ProviderError:
                    installed = []
                if installed and model not in installed:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"« {model} » n'est pas installé : télécharge-le d'abord "
                            "(bouton Télécharger de cet onglet)."
                        ),
                    )
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
            if not model:
                spec = catalog().get(provider)
                model = spec.models[0].id if spec and spec.models else ""
        else:
            raise HTTPException(status_code=400, detail="choice must be ollama, huggingface or cloud")

        try:
            write_layer("user", "default_provider", provider)
            if model:
                write_layer("user", "default_model", model)
        except OSError as exc:
            raise HTTPException(
                status_code=409,
                detail=(
                    "config.toml est en lecture seule : UH n'a pas pu enregistrer ce choix "
                    f"({exc}). Ferme l'éditeur qui le tient ouvert, puis réessaie."
                ),
            ) from exc
        return {"saved": True, "provider": provider, "model": model}

    @app.get("/api/mcp/cloud", dependencies=[Depends(require_token)])
    async def mcp_cloud_catalog() -> dict:
        """The templates the UI may offer, and which servers can already store a file."""
        from .. import mcp_cloud
        from .. import mcp_http as uh_mcp_http

        def _http_rows() -> list:
            """Recorded HTTP entries, each with the state this process can prove.

            The secrets file supplies the masked token, the transport supplies the
            live state: the panel gets one row per entry and never a value it could
            replay.
            """
            recorded = {row["name"]: row for row in mcp_cloud.pending_http_servers()}
            live = {row["name"]: row for row in uh_mcp_http.status()}
            rows = []
            for name, base in recorded.items():
                row = {**base, **{k: v for k, v in live.get(name, {}).items() if k != "url"}}
                row.setdefault("state", "configured")
                row.setdefault("label", "configuré")
                row.setdefault("tools", [])
                rows.append(row)
            for name, row in live.items():
                if name not in recorded:
                    rows.append(row)
            return rows

        return {
            "templates": mcp_cloud.template_list(),
            "upload_targets": await asyncio.to_thread(mcp_cloud.upload_targets),
            # Recorded entries with their live state: configured, connected,
            # jeton refusé, injoignable or protocole incompatible (P14.1).
            "http_servers": await asyncio.to_thread(_http_rows),
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

        The entry is written, then asked: the handshake happens in this same
        gesture, and the answer carries the state the panel shows. The stdio
        client still skips the entry - a URL belongs to `uh.mcp_http`.
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
                allow_insecure=bool(body.get("allow_insecure")),
            )
        except mcp_cloud.CloudError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Recording is not connecting: the entry is written, then asked. A save
        # that needs a second gesture ("now press Test") is a save that leaves
        # the user with "configured" and no idea whether it works.
        from .. import mcp_http as uh_mcp_http

        connection = await asyncio.to_thread(uh_mcp_http.test_connection, created["name"])
        return {**created, "connection": connection, "state": connection.get("state")}

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

    @app.post("/api/mcp/import", dependencies=[Depends(require_token)])
    async def mcp_import_file(payload: Annotated[dict, Body()]) -> dict:
        """Read an MCP configuration file (JSON, or the documented YAML subset).

        Nothing is saved: the popup fills its fields with the answer, the user
        reviews them, tests, then saves. A file that holds several servers comes
        back as several entries.
        """
        from .. import mcp_import

        body = payload or {}
        try:
            found = await asyncio.to_thread(
                mcp_import.parse,
                str(body.get("text") or ""),
                str(body.get("filename") or ""),
            )
        except mcp_import.MCPImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"servers": found}

    @app.post("/api/mcp/probe", dependencies=[Depends(require_token)])
    async def mcp_probe(payload: Annotated[dict, Body()]) -> dict:
        """Test a candidate server *before* it is saved. Writes nothing.

        The popup keeps its "Enregistrer" button disabled until this answers
        ``connected``: a broken entry never lands in ``config.toml``. The whole
        handshake is capped at five seconds, and the token is used, never stored.
        """
        from .. import mcp as uh_mcp
        from .. import mcp_http as uh_mcp_http
        from .. import urlguard
        from ..providers_admin import is_loopback_bind

        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail="testing a server is only allowed when uh web listens on loopback",
            )
        body = payload or {}
        name = str(body.get("name") or "candidat").strip() or "candidat"
        transport = str(body.get("transport") or "http").strip().lower()
        timeout = 5.0
        if transport in {"http", "sse"}:
            try:
                checked = urlguard.check(
                    str(body.get("url") or ""),
                    allow_insecure=bool(body.get("allow_insecure")),
                )
            except urlguard.URLRejected as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            server = uh_mcp_http.HTTPServer(
                name=name,
                url=checked.url,
                transport=transport,
                token=str(body.get("token") or ""),
                timeout=timeout,
                insecure=checked.insecure,
            )
            return await asyncio.to_thread(uh_mcp_http.probe, server)

        command = str(body.get("command") or "").strip()
        if not command:
            raise HTTPException(status_code=400, detail="command is required for a stdio server")
        raw_args = body.get("args") or []
        if isinstance(raw_args, str):
            raw_args = raw_args.splitlines()
        env = body.get("env") or {}
        if not isinstance(env, dict):
            raise HTTPException(status_code=400, detail="env must be an object of KEY=value")
        candidate = uh_mcp.ServerConfig(
            name=name,
            command=command,
            args=tuple(str(item).strip() for item in raw_args if str(item).strip()),
            env={str(key): str(value) for key, value in env.items() if str(key).strip()},
            timeout=timeout,
        )
        return await asyncio.to_thread(uh_mcp.probe_server, candidate, timeout=timeout)

    @app.post("/api/mcp/{name}/test", dependencies=[Depends(require_token)])
    async def mcp_test(name: str) -> dict:
        """Ask one configured MCP server a question *now*.

        An HTTP/SSE entry is asked over its own transport (P14.1); a stdio entry
        is started, listed and stopped. Either way the answer is never served from
        the process cache, so a green dot here means this second.
        """
        from .. import mcp
        from .. import mcp_http as uh_mcp_http

        if any(server.name == name for server in await asyncio.to_thread(uh_mcp_http.servers_from_config)):
            return await asyncio.to_thread(uh_mcp_http.test_connection, name)

        servers, _ = await asyncio.to_thread(mcp.servers_from_config)
        if not any(server.name == name for server in servers):
            raise HTTPException(status_code=404, detail=f"no MCP server named '{name}'")
        try:
            return await asyncio.to_thread(mcp.test_server, name)
        except mcp.MCPError as exc:
            # The server's stderr is quoted in the message; redaction happens in
            # `stderr_tail`, and again here for any path that formatted a secret.
            raise HTTPException(
                status_code=502, detail=_redact(str(exc))
            ) from exc

    # -- remote tool approval (P14.1) -------------------------------------- #
    @app.get("/api/mcp/approvals", dependencies=[Depends(require_token)])
    async def mcp_approvals() -> dict:
        """Questions a remote tool is waiting on right now.

        The SSE stream carries each one when it is asked; this route exists for a
        page that missed the frame (reload, second window) and must not leave a
        call hanging behind a banner nobody can see.
        """
        from .. import mcp_http as uh_mcp_http

        return {"pending": uh_mcp_http.GATE.pending()}

    @app.post("/api/mcp/approvals/{ticket}", dependencies=[Depends(require_token)])
    async def mcp_approve(ticket: str, payload: Annotated[dict, Body()]) -> dict:
        """Answer one question: ``{"allow": true}`` runs the call, anything else refuses."""
        from .. import mcp_http as uh_mcp_http
        from ..providers_admin import is_loopback_bind

        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail="answering an approval is only allowed when uh web listens on loopback",
            )
        body = payload or {}
        resolved = uh_mcp_http.GATE.resolve(
            ticket, bool(body.get("allow")), always=bool(body.get("always"))
        )
        if not resolved:
            raise HTTPException(
                status_code=404,
                detail="cette demande a expiré ou n'existe plus",
            )
        return {
            "resolved": True,
            "ticket": ticket,
            "allow": bool(body.get("allow")),
            "always": bool(body.get("always")),
            "trusted": uh_mcp_http.GATE.trusted(),
        }

    @app.post("/api/mcp/{name}/call", dependencies=[Depends(require_token)])
    async def mcp_call(name: str, payload: Annotated[dict, Body()]) -> dict:
        """Run one read-only tool for a "Tester" button, and time it.

        The human is the approver: they clicked. Only :data:`MCP_PROBE_TOOLS` is
        reachable this way, so a bug in the panel cannot become a remote write,
        and the answer carries the latency - a green dot without a number is a
        promise, not a measurement.
        """
        from .. import mcp_http as uh_mcp_http
        from ..providers_admin import is_loopback_bind

        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403,
                detail="tester un serveur MCP n'est permis que si uh web écoute en loopback",
            )
        body = payload or {}
        tool = str(body.get("tool") or "list_sessions").strip()
        if not MCP_PROBE_TOOL_RE.match(tool):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"« {tool} » n'est pas un outil de test en lecture seule ; "
                    f"autorisés : {MCP_PROBE_TOOLS_HELP}"
                ),
            )
        server = next(
            (item for item in uh_mcp_http.servers_from_config() if item.name == name), None
        )
        if server is None:
            known = ", ".join(item.name for item in uh_mcp_http.servers_from_config()) or "aucun"
            raise HTTPException(
                status_code=404, detail=f"aucun serveur MCP HTTP nommé « {name} » (connus : {known})"
            )
        started = time.perf_counter()
        try:
            client = await asyncio.to_thread(uh_mcp_http.client_for, server)
            result = await asyncio.to_thread(client.call, tool, dict(body.get("arguments") or {}))
        except uh_mcp_http.MCPError as exc:
            return {
                "ok": False,
                "server": name,
                "tool": tool,
                "error": str(exc),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
        return {
            "ok": True,
            "server": name,
            "tool": tool,
            "result": result,
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }

    @app.post("/api/system/quit", dependencies=[Depends(require_token)])
    async def system_quit(payload: Annotated[dict, Body()]) -> dict:
        """Quit UH: stop the second brain, then end this process.

        Closing the browser window does not stop a server, so `uhb.exe` outlived
        what the user thought they had closed. One button, one honest shutdown:
        the answer is sent first, then the process ends - with UHB already stopped,
        so nothing is left behind even when the job object was refused.
        """
        from .. import uhb_spawn
        from ..providers_admin import is_loopback_bind

        if not is_loopback_bind(str(getattr(app.state.settings, "host", ""))):
            raise HTTPException(
                status_code=403, detail="quitter UH n'est permis que si le serveur écoute en loopback"
            )
        if not (payload or {}).get("confirm"):
            raise HTTPException(status_code=400, detail='envoie {"confirm": true} pour quitter')
        active = [key for key in getattr(app.state, "active", {})]
        if active:
            raise HTTPException(
                status_code=409,
                detail="un tour est en cours : arrête-le (Stop) avant de quitter UH",
            )
        stopped = await asyncio.to_thread(uhb_spawn.stop_running)

        async def end_soon() -> None:
            # The response must reach the browser, then the process ends: uvicorn
            # has no "exit now" path, and a half-stopped server holding the port is
            # exactly the state this route exists to remove.
            await asyncio.sleep(0.5)
            LOGGER.warning("quit requested from the interface: stopping uh web")
            os._exit(0)

        asyncio.get_running_loop().create_task(end_soon())
        return {"quitting": True, "uhb_stopped": stopped}

    # -- search / models --------------------------------------------------- #
    @app.get("/api/search", dependencies=[Depends(require_token)])
    async def search(q: str, limit: int = 20) -> dict:
        with _store(app) as store:
            hits = store.search(q, limit=limit)
        return {"query": q, "hits": [{**hit, "short_id": display_id(hit["session_id"])} for hit in hits]}

    @app.get("/api/models", dependencies=[Depends(require_token)])
    async def models() -> dict:
        """What the header may offer, and who would serve it.

        ``choices`` is the menu: local models first (Ollama), then every API
        provider that actually holds a key, with the model ids its catalogue
        publishes. A provider with no key is left out on purpose - offering a
        model that answers 401 is a trap, not a choice. ``ollama`` and
        ``model`` stay for older clients.
        """
        from ..keys import read_providers as read_custom
        from ..providers import all_provider_kinds
        from ..providers_admin import catalog as provider_catalog
        from ..providers_admin import read_providers

        def _local_menu() -> list[dict]:
            """Discovery and capability probes, off the event loop.

            ``list_models`` and ``/api/show`` are synchronous httpx calls. Inside an
            ``async def`` route they froze the server for up to 1.5 s plus 0.6 s per
            local model - five models meant a multi-second stall on every menu load,
            which reads exactly like "the app stopped answering".
            """
            from ..providers.ollama import list_models

            try:
                return [
                    {
                        "name": str(entry["name"]),
                        "size": int(entry.get("size") or 0),
                        # Asked, not assumed: a local model that cannot call tools is
                        # the usual reason a turn comes back as a code block.
                        "tools_ok": _local_tools_ok(str(entry["name"])),
                    }
                    for entry in list_models(timeout=1.5)
                ]
            except ProviderError:
                return []

        local_rows = await asyncio.to_thread(_local_menu)
        local = [row["name"] for row in local_rows]

        choices: list[dict] = [
            {
                "id": row["name"],
                "provider": "ollama",
                "local": True,
                "note": "",
                # The menu sorts local models by weight: the biggest one is the one
                # worth trying first, and a flat alphabetical list said nothing.
                "size": row["size"],
                "tools_ok": row["tools_ok"],
            }
            for row in local_rows
        ]
        configured = read_providers()
        for spec_name, spec in provider_catalog().items():
            entry = configured.get(spec_name) or {}
            if not entry.get("keys"):
                continue
            for model_spec in spec.models:
                choices.append(
                    {
                        "id": model_spec.id,
                        "provider": spec_name,
                        "local": False,
                        "note": "outils" if model_spec.tools else "",
                        "tools_ok": bool(model_spec.tools),
                    }
                )
        for name, entry in read_custom().items():
            # A catalogue provider is read from both stores (its key lives in
            # keys.toml too): the catalogue loop above already offered it.
            if name in provider_catalog():
                continue
            if entry.get("model"):
                choices.append(
                    {
                        "id": str(entry["model"]),
                        "provider": name,
                        "local": False,
                        "note": "",
                        # A hand-written provider entry says nothing about tools.
                        "tools_ok": None,
                    }
                )

        current = {
            "id": app.state.model,
            "provider": app.state.kind,
            "local": app.state.kind == "ollama",
            "note": "actif",
            "tools_ok": (
                _local_tools_ok(str(app.state.model)) if app.state.kind == "ollama" else None
            ),
        }
        if not any(
            row["id"] == current["id"] and row["provider"] == current["provider"]
            for row in choices
        ):
            choices.insert(0, current)

        return {
            "provider": app.state.kind,
            "model": app.state.model,
            "current": current,
            "choices": choices,
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
        """Switch the pair, not the half-pair: a model id belongs to a provider.

        The header sends both. When it sends only a name, the provider is inferred
        from the catalogues (a local name is Ollama, a published id is its
        provider, a ``provider/model`` ref is split). A local name with an API
        provider is refused here with a sentence: that mismatch used to reach the
        API and come back as a 500.
        """
        model = str((payload or {}).get("model") or "").strip()
        session_id = (payload or {}).get("session_id")
        if not model:
            raise HTTPException(status_code=400, detail="model is required")
        kind = str((payload or {}).get("provider") or "").strip().lower()

        local: list[str] = []
        try:
            from ..providers.ollama import list_models

            local = [entry["name"] for entry in list_models(timeout=1.5)]
        except ProviderError:
            local = []

        note = ""
        if not kind:
            kind, model, note = _infer_provider(model, app.state.kind, local)
        if ":" in kind or "/" in kind:
            raise HTTPException(status_code=400, detail=f"unknown provider '{kind}'")
        if model in local and kind != "ollama":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"« {model} » est un modèle local : choisis le fournisseur ollama, "
                    f"pas {kind}."
                ),
            )
        if kind == "ollama" and local and model not in local:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"« {model} » n'est pas installé sur cette machine : télécharge-le "
                    f"d'abord depuis l'onglet IA locale."
                ),
            )
        try:
            app.state.provider_factory(kind, model)
        except ProviderError as exc:
            raise HTTPException(status_code=400, detail=_provider_detail(exc, kind, model)) from exc
        # The header switch is the product's default, not a per-tab whim: it goes
        # to config.toml, so the next `uh web` starts on the same model instead of
        # silently falling back to the first local one.
        persisted = True
        try:
            uh_config.write_layer("user", "default_provider", kind)
            uh_config.write_layer("user", "default_model", model)
        except uh_config.ConfigError:
            # The flat writer refuses a config that also holds tables
            # ([[mcp.servers]]). One root line can still be set without touching
            # them, which is the difference between "my model is remembered" and
            # "my model resets at every restart".
            try:
                uh_config.write_flat_key_preserving_tables("default_provider", kind)
                uh_config.write_flat_key_preserving_tables("default_model", model)
            except (uh_config.ConfigError, OSError):
                persisted = False
        if session_id:
            session = _session_or_404(app, str(session_id))
            app.state.session_models[session["id"]] = (kind, model)
            return {
                "session": display_id(session["id"]),
                "provider": kind,
                "model": model,
                "note": note,
                "persisted": persisted,
            }
        app.state.kind, app.state.model = kind, model
        return {"provider": kind, "model": model, "note": note, "persisted": persisted}

    # Jalon 7a: provider panel. Imported late to keep the module import light.
    from .providers_api import register_provider_routes

    register_provider_routes(app, require_token)

    return app


def _infer_provider(model: str, current_kind: str, local: list[str]) -> tuple[str, str, str]:
    """Which provider serves ``model``, when the caller did not say.

    Order: an explicit ``provider/model`` ref, then the installed local list, then
    the catalogues. The current provider is the last resort, so an unknown name
    keeps the previous behaviour instead of silently switching engines.
    """
    if "/" in model and not model.startswith(("http://", "https://")):
        head, tail = model.split("/", 1)
        try:
            from ..providers import all_provider_kinds

            if head.strip().lower() in all_provider_kinds():
                return head.strip().lower(), tail.strip(), ""
        except Exception:  # noqa: BLE001 - inference never blocks a switch
            pass
    if model in local:
        return "ollama", model, ""
    try:
        from ..providers_admin import catalog as provider_catalog

        for name, spec in provider_catalog().items():
            if any(entry.id == model for entry in spec.models):
                return name, model, ""
    except Exception:  # noqa: BLE001 - a broken catalogue is not a refused switch
        pass
    return current_kind or "ollama", model, ""


def _provider_detail(exc: Exception, kind: str, model: str) -> str:
    """A sentence the panel can show: what failed, and what to do about it."""
    status = getattr(exc, "status", None)
    text = str(exc)
    if kind == "ollama":
        lowered = text.lower()
        if status == 404 or "not found" in lowered or "no such model" in lowered:
            return f"Ollama n'a pas « {model} » : télécharge-le depuis l'onglet IA locale."
        if status is not None:
            return (
                f"Ollama a refusé la requête ({status}). Vérifie qu'il tourne toujours, "
                "puis réessaie."
            )
        return f"Ollama est injoignable : ouvre l'application, puis réessaie. ({text})"
    if status == 401:
        return f"La clé {kind} est refusée : vérifie-la dans l'onglet Clé API."
    if status == 402:
        return f"Le compte {kind} n'a plus de crédit."
    if status == 429:
        return f"{kind} limite le débit : attends une minute, puis réessaie."
    return f"{kind} n'a pas pu servir « {model} » : {text}"


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


def check_bind(host: str, *, allow_remote: bool = False) -> Optional[str]:
    """``None`` when the bind is acceptable, else the sentence to print.

    The cockpit is a local API: it can register MCP servers, write config and run
    tools. Its only credential is a token in the URL, so listening beyond
    loopback is not a "warning" - it is a different security model. It therefore
    needs ``--allow-remote``, which is the user saying so on purpose.
    """
    value = (host or "").strip()
    if value in LOOPBACK_NAMES:
        return None
    if allow_remote:
        return None
    return (
        f"uh web refuse d'écouter sur {value} : l'interface modifie la configuration et "
        "lance des outils, et son seul secret est le jeton de l'URL. Reste sur 127.0.0.1, "
        "ou ajoute --allow-remote si tu sais ce que ça implique."
    )


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
    allow_remote: bool = False,
    rotate_token: bool = False,
) -> int:
    """Start uvicorn and print the tokenised URL. Returns the exit code."""
    import uvicorn

    refusal = check_bind(host, allow_remote=allow_remote)
    if refusal is not None:
        print(refusal, file=sys.stderr)
        return 1

    # A busy port used to be uvicorn's exit code 3 and nothing else: the user saw
    # a popup "uh web stopped" with no cause. An old `uh web` still holding the
    # port is the common case, and the interface it serves is read from disk - so
    # the browser gets the new files and the server does not know the new routes
    # ("Not Found" on a button that exists). Move to the next free port instead.
    if _port_busy(host, port):
        version = _uh_version_on(host, port)
        if version:
            # Two copies of the product on one host is the bug behind "the UI is
            # broken 99% of the time": the browser gets files from disk and routes
            # from an older process. Say it, and point at the one that answers.
            token = _stable_token(rotate=False)
            url = f"http://{host}:{port}/?token={token}"
            print(
                f"uh web: {host}:{port} sert déjà UH {version} (autre processus).",
                file=sys.stderr,
            )
            print(f"  ouvre cette instance : {url}", file=sys.stderr)
            print(
                "  ou lance celle-ci ailleurs : uh web --port <autre port> ;"
                " `uh web --quit-existing` arrête l'autre.",
                file=sys.stderr,
            )
            if open_browser:
                _open_url(url, app_window=app_window)
            return 0
        free = first_free_port(host, port + 1) if port_fallback else None
        if free is None:
            where = f"{port}-{port + 9}" if port_fallback else str(port)
            print(
                f"uh web: {host}:{where} {'are all in use' if port_fallback else 'is already in use'}"
                " by something that is not UH. Stop it or pass --port.",
                file=sys.stderr,
            )
            return 1
        print(f"uh web: {host}:{port} is used by another program; using {free}")
        port = free

    app = create_app(
        host=host,
        port=port,
        token=_stable_token(rotate=rotate_token),
        allow_terminal=allow_terminal,
        workspace=workspace,
        static_dir=static_dir,
    )
    if os.environ.get("UH_DEV_RELOAD") == "1":
        # A route added after the server started is a 404 until the process is
        # restarted (routes are frozen at import, the static files are not).
        # UH_DEV_RELOAD=1 makes uvicorn watch the package: the class of bug the
        # message in app.js describes never happens while working on the UI.
        os.environ.update(
            {
                "UH_WEB_HOST": host,
                "UH_WEB_PORT": str(port),
                "UH_WEB_TOKEN": app.state.token,
                "UH_WEB_ALLOW_TERMINAL": "1" if allow_terminal else "0",
                "UH_WEB_WORKSPACE": str(app.state.settings.workspace),
            }
        )
        print(f"UH web (dev, auto-reload) - http://{host}:{port}/?token={app.state.token}")
        print("  UH_DEV_RELOAD=1: a Python change restarts the worker, the token stays.")
        uvicorn.run(
            "uh.web.dev:app",
            factory=True,
            reload=True,
            reload_dirs=[str(Path(__file__).parent)],
            host=host,
            port=port,
            log_level="info",
        )
        return 0
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
