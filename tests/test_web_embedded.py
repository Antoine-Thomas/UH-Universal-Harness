"""`/web` starts the UI in a thread: real uvicorn, real socket, no model.

The point of these tests is the *contract*, not the HTTP surface (``test_web.py``
covers the routes): the UI must come up off the main thread, answer, refuse to
bind twice, and die when the console exits. A stubbed provider keeps every test
offline and fast; ``open_browser=False`` keeps a browser window out of it.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

import httpx  # noqa: E402

from uh import (
    cli,  # noqa: E402
    commands,  # noqa: E402
)
from uh import config as uh_config  # noqa: E402
from uh import console as uh_console  # noqa: E402
from uh.web import embedded  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-embedded-0123456789"


class Stub:
    model = "stub:1b"

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


def free_port() -> int:
    """A port nothing listens on, right now (closed before it is handed over)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def factory(tmp_path: Path):
    """create_app with a stub provider and a fixed token: no discovery."""

    def build(**kwargs):
        kwargs.setdefault("workspace", tmp_path)
        return create_app(
            token=TOKEN,
            provider_factory=lambda kind, model: Stub(),
            kind="stub",
            model="stub:1b",
            **kwargs,
        )

    return build


@pytest.fixture
def ui(tmp_path: Path):
    """One embedded UI, stopped even when an assertion fails."""
    server = embedded.start(
        port=free_port(),
        open_browser=False,
        workspace=tmp_path,
        app_factory=factory(tmp_path),
    )
    try:
        yield server
    finally:
        server.stop()


# --------------------------------------------------------------------------- #
# start / stop
# --------------------------------------------------------------------------- #
def test_the_ui_comes_up_off_the_main_thread(ui: embedded.EmbeddedServer) -> None:
    """uvicorn serves from its own thread: the REPL keeps the main one."""
    assert ui.alive
    worker = next(t for t in threading.enumerate() if t.name == embedded.THREAD_NAME)
    assert worker is not threading.main_thread()
    assert worker.is_alive()

    response = httpx.get(ui.url, timeout=5.0)

    assert response.status_code == 200
    assert response.headers.get("content-security-policy", "").startswith("default-src 'none'")
    assert "text/html" in response.headers.get("content-type", "")


def test_stop_frees_the_port(ui: embedded.EmbeddedServer) -> None:
    port = ui.port
    assert ui.alive

    ui.stop()

    assert not ui.alive
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))  # would raise if uvicorn still held it


def test_stop_is_idempotent(ui: embedded.EmbeddedServer) -> None:
    ui.stop()
    ui.stop()  # second call must not raise
    assert not ui.alive


def test_start_in_background_refuses_to_bind_twice(tmp_path: Path) -> None:
    port = free_port()
    first, started = embedded.start_in_background(
        port=port,
        open_browser=False,
        workspace=tmp_path,
        app_factory=factory(tmp_path),
    )
    try:
        assert started is True
        again, started_again = embedded.start_in_background(
            port=free_port(),  # a different port would have been free: it must not matter
            open_browser=False,
            workspace=tmp_path,
            app_factory=factory(tmp_path),
        )
        assert started_again is False
        assert again is first
        assert again.url == first.url
        assert embedded.running() is first
    finally:
        embedded.stop_running()

    assert embedded.running() is None


def test_a_busy_port_is_one_readable_error(tmp_path: Path) -> None:
    holder, _ = embedded.start_in_background(
        port=free_port(),
        open_browser=False,
        workspace=tmp_path,
        app_factory=factory(tmp_path),
    )
    try:
        with pytest.raises(embedded.WebStartError) as caught:
            embedded.start(
                port=holder.port,
                open_browser=False,
                workspace=tmp_path,
                app_factory=factory(tmp_path),
                timeout=5.0,
            )
        message = str(caught.value)
        assert "Port" in message or "port" in message
        assert "Traceback" not in message
    finally:
        embedded.stop_running()


def test_a_missing_extra_is_one_readable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No [web] extra: an install hint, not an ImportError traceback."""
    monkeypatch.setitem(sys.modules, "uvicorn", None)  # `import uvicorn` now fails

    with pytest.raises(embedded.WebStartError) as caught:
        embedded.start(port=free_port(), open_browser=False)

    assert "[web]" in str(caught.value)


# --------------------------------------------------------------------------- #
# /web in the registry
# --------------------------------------------------------------------------- #
class _FakeEmbedded:
    url = "http://127.0.0.1:8765/?token=t"
    port = 8765
    alive = True

    def stop(self) -> None:
        self.alive = False


def _session(tmp_path: Path) -> uh_console.ChatSession:
    stub = Stub()
    return uh_console.ChatSession(
        provider_factory=lambda kind, model: stub,
        provider_kind="stub",
        chain=[("stub", stub)],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=True,
        store=None,
        max_iterations=2,
    )


def test_slash_web_without_argument_starts_the_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict] = []

    def start_in_background(**kwargs):
        seen.append(kwargs)
        return _FakeEmbedded(), True

    monkeypatch.setattr(embedded, "start_in_background", start_in_background)

    result = commands.dispatch(_session(tmp_path), "/web")

    assert result.err == ""
    assert "web UI: http://127.0.0.1:8765/?token=t" in result.out
    assert "/exit stops the server" in result.out
    assert seen and seen[0]["workspace"] == tmp_path, "the UI opens on the session workspace"


def test_a_second_slash_web_reports_the_url_already_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedded, "start_in_background", lambda **kw: (_FakeEmbedded(), False))

    result = commands.dispatch(_session(tmp_path), "/web")

    assert "already running" in result.out
    assert "http://127.0.0.1:8765/?token=t" in result.out
    assert result.err == ""


def test_slash_web_reports_a_failed_start_as_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs):
        raise embedded.WebStartError("the [web] extra is not installed. install it with: pip")

    monkeypatch.setattr(embedded, "start_in_background", boom)

    result = commands.dispatch(_session(tmp_path), "/web")

    assert result.out == ""
    assert result.err.startswith("web: the [web] extra is not installed")


def test_slash_web_with_a_query_still_searches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-argument path must not swallow the search path."""

    def must_not_start(**kwargs):  # pragma: no cover - the assertion is the point
        raise AssertionError("/web <query> must not start the UI")

    monkeypatch.setattr(embedded, "start_in_background", must_not_start)

    class _Response:
        status_code = 200
        text = "<html></html>"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response())

    result = commands.dispatch(_session(tmp_path), "/web une requête")

    assert "web search: une requête" in result.out or "no results" in result.out


# --------------------------------------------------------------------------- #
# the console owns the server's lifetime
# --------------------------------------------------------------------------- #
def test_exit_stops_a_ui_started_by_slash_web(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, _ = embedded.start_in_background(
        port=free_port(),
        open_browser=False,
        workspace=tmp_path,
        app_factory=factory(tmp_path),
    )
    assert server.alive
    monkeypatch.setattr("builtins.input", lambda prompt="": "/exit")

    assert cli._run_repl(_session(tmp_path)) == 0

    assert not server.alive, "/exit must stop the UI"
    assert embedded.running() is None


def test_the_console_keeps_answering_while_the_ui_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REPL is not blocked: a prompt is answered while uvicorn serves."""
    server, _ = embedded.start_in_background(
        port=free_port(),
        open_browser=False,
        workspace=tmp_path,
        app_factory=factory(tmp_path),
    )
    answers = iter(["bonjour", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    session = _session(tmp_path)
    started = time.monotonic()

    assert cli._run_repl(session) == 0
    elapsed = time.monotonic() - started

    assert server.alive is False  # stopped by the exit path
    assert elapsed < 20.0, "a turn while uvicorn runs must not wait for the server"
    assert [m["role"] for m in session.history].count("assistant") == 1
