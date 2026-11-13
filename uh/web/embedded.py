"""The web UI started from inside the console (``/web`` with no argument).

``uh web`` blocks the terminal by design. ``/web`` must not: the console has to
keep answering prompts while the browser UI runs, so the same ASGI app is served
by a ``uvicorn.Server`` in a daemon thread and stopped on demand.

Three consequences worth knowing:

* **no signal handler is installed** - uvicorn skips them off the main thread,
  and shutdown is explicit (``should_exit``). Ctrl+C still belongs to the REPL;
* **one server per process** - a second ``/web`` reports the URL already served
  instead of binding twice. Two servers on one port is an error, and two tokens
  are a support question nobody wants;
* **it dies with the console** - ``/exit``, EOF and Ctrl+C all stop it, so no
  orphan process is left holding port 8765.

``fastapi`` and ``uvicorn`` are imported inside the functions: this module stays
importable on a minimal install, where ``/web`` answers with the install hint
rather than an ImportError.

Nothing here writes to stdout: the console owns the screen, and uvicorn runs at
``warning`` so an access log cannot scroll a conversation away.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

THREAD_NAME = "uh-web"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
START_TIMEOUT = 10.0
STOP_TIMEOUT = 5.0
EXTRA_HINT = 'install it with: pip install "uh-harness[web]"'


class WebStartError(RuntimeError):
    """The UI could not start. The message is user-facing, never a traceback."""


class EmbeddedServer:
    """A UI running in the background: its URL, its thread, how to stop it."""

    def __init__(self, server: Any, thread: threading.Thread, url: str, port: int) -> None:
        self._server = server
        self._thread = thread
        self.url = url
        self.port = port

    @property
    def alive(self) -> bool:
        """False once uvicorn's thread has returned (crash, or stopped)."""
        return self._thread.is_alive()

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Ask uvicorn to shut down and wait for its thread. Idempotent."""
        self._server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)


def _open_browser(url: str) -> None:
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box has no browser
        pass


def start(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    allow_terminal: bool = False,
    workspace: Optional[Path] = None,
    static_dir: Optional[Path] = None,
    timeout: float = START_TIMEOUT,
    app_factory: Optional[Any] = None,
) -> EmbeddedServer:
    """Serve the UI in a daemon thread and wait until it accepts connections.

    Raises :class:`WebStartError` with a readable reason - extra not installed,
    port taken, never ready - so ``/web`` prints one line instead of a traceback.
    ``app_factory`` is the seam the tests use to build an app with a stubbed
    provider, so no test needs a model or a network.
    """
    try:
        import uvicorn

        from .app import create_app
    except ImportError as exc:
        raise WebStartError(
            f"the [web] extra is not installed ({type(exc).__name__}: {exc}). {EXTRA_HINT}"
        ) from exc

    factory = app_factory or create_app
    app = factory(
        host=host,
        port=port,
        allow_terminal=allow_terminal,
        workspace=workspace,
        static_dir=static_dir,
    )
    # log_level=warning: the console owns the screen, uvicorn must not scroll it.
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    failure: list[BaseException] = []

    def _serve() -> None:
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller
            failure.append(exc)

    thread = threading.Thread(target=_serve, name=THREAD_NAME, daemon=True)
    thread.start()

    deadline = time.monotonic() + timeout
    while not server.started:
        if failure:
            reason = f"{type(failure[0]).__name__}: {failure[0]}"
            raise WebStartError(f"the UI did not start ({reason}). Port {port} may be taken.")
        if not thread.is_alive():
            raise WebStartError(f"the UI stopped before it was ready. Port {port} may be taken.")
        if time.monotonic() >= deadline:
            server.should_exit = True
            raise WebStartError(f"the UI did not answer within {timeout:.0f}s.")
        time.sleep(0.05)

    url = f"http://{host}:{port}/?token={app.state.token}"
    if open_browser:
        threading.Timer(1.0, _open_browser, args=(url,)).start()
    return EmbeddedServer(server, thread, url, port)


# --------------------------------------------------------------------------- #
# the one server per process
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_RUNNING: Optional[EmbeddedServer] = None


def running() -> Optional[EmbeddedServer]:
    """The server this process serves, or ``None``."""
    return _RUNNING


def start_in_background(**kwargs: Any) -> tuple[EmbeddedServer, bool]:
    """Start the UI once. Returns ``(server, started_now)``.

    ``started_now=False`` means a live server was already there: the caller
    reports its URL instead of binding the port a second time. A server whose
    thread died is replaced rather than reported as running.
    """
    global _RUNNING
    with _LOCK:
        if _RUNNING is not None and _RUNNING.alive:
            return _RUNNING, False
        _RUNNING = start(**kwargs)
        return _RUNNING, True


def stop_running(timeout: float = STOP_TIMEOUT) -> bool:
    """Stop the embedded UI when there is one. True when something was stopped."""
    global _RUNNING
    with _LOCK:
        server, _RUNNING = _RUNNING, None
    if server is None:
        return False
    server.stop(timeout)
    return True


__all__ = [
    "EmbeddedServer",
    "WebStartError",
    "start",
    "running",
    "start_in_background",
    "stop_running",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "START_TIMEOUT",
    "STOP_TIMEOUT",
]
