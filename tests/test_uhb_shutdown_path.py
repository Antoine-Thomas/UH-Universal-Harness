"""Quitting UH stops UHB the way UHB decided, and falls back without lying.

`POST /shutdown` (UHB) owns the stopping policy: token, `confirm`, targets,
verification. Before this lot, `stop_running()` terminated the process tree and
nothing else — so quitting `uh web` left `docker compose` containers, the local
RAG and the SiYuan kernel running, holding 8101/8200/6806 and the RAM. That is
the duplicated business rule this file pins: the route is asked first, and the
process tree is only the fallback.

Four cases, exactly the ones the brief lists: route accepts, route refuses,
no token, UHB already down. Plus one that matters more than any of them: the
token never reaches a log, an exception message or an argv.
"""

from __future__ import annotations

from typing import Optional

import httpx
import pytest

from uh import uhb_spawn


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Client:
    """Records what would have been sent, and answers with a fixed status."""

    def __init__(self, status_code: int = 202, error: Optional[Exception] = None) -> None:
        self.status_code = status_code
        self.error = error
        self.calls: list[dict] = []

    def post(self, url: str, *, json=None, headers=None) -> _Response:
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if self.error is not None:
            raise self.error
        return _Response(self.status_code)


class _Child:
    """A spawned UHB, reduced to what `stop_running` uses."""

    def __init__(self) -> None:
        self.stopped: list[float] = []

    def stop(self, timeout: float = 0.0) -> None:
        self.stopped.append(timeout)


@pytest.fixture()
def token(tmp_path, monkeypatch):
    """A token in a per-test file: never the operator's ~/.uhb."""
    path = tmp_path / "shutdown-token"
    path.write_text("test-token-not-a-secret\n", encoding="utf-8")
    monkeypatch.delenv(uhb_spawn.SHUTDOWN_TOKEN_ENV, raising=False)
    monkeypatch.setenv(uhb_spawn.SHUTDOWN_TOKEN_FILE_ENV, str(path))
    return "test-token-not-a-secret"


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #
def test_the_route_is_asked_for_self_only(token: str) -> None:
    client = _Client(202)

    assert uhb_spawn.request_shutdown("http://127.0.0.1:8101", client=client) is True

    call = client.calls[0]
    assert call["url"] == "http://127.0.0.1:8101/shutdown"
    assert call["json"] == {"confirm": True, "stop": ["self"]}, (
        "a quit must not widen the perimeter to docker or local services"
    )
    assert call["headers"]["X-UHB-Token"] == token


def test_a_refused_route_is_a_false_not_an_exception(token: str) -> None:
    client = _Client(401)

    assert uhb_spawn.request_shutdown("http://127.0.0.1:8101", client=client) is False


def test_without_a_token_nothing_is_sent(monkeypatch, tmp_path) -> None:
    """No token, no request: 401 noise for a route the caller cannot use."""
    monkeypatch.delenv(uhb_spawn.SHUTDOWN_TOKEN_ENV, raising=False)
    monkeypatch.setenv(uhb_spawn.SHUTDOWN_TOKEN_FILE_ENV, str(tmp_path / "absent"))
    client = _Client(202)

    assert uhb_spawn.request_shutdown("http://127.0.0.1:8101", client=client) is False
    assert client.calls == []


def test_an_unreachable_uhb_is_a_false(token: str) -> None:
    client = _Client(error=httpx.ConnectError("refused"))

    assert uhb_spawn.request_shutdown("http://127.0.0.1:8101", client=client) is False


# --------------------------------------------------------------------------- #
# the caller
# --------------------------------------------------------------------------- #
def test_stop_running_prefers_the_route_then_falls_back(monkeypatch, token) -> None:
    child = _Child()
    monkeypatch.setattr(uhb_spawn, "_RUNNING", child)
    monkeypatch.setattr(uhb_spawn, "request_shutdown", lambda *a, **k: False)
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)

    assert uhb_spawn.stop_running() is True
    assert child.stopped, "the process tree must still be terminated when the route declines"


def test_stop_running_waits_for_the_port_after_a_202(monkeypatch, token) -> None:
    child = _Child()
    seen: list[float] = []
    monkeypatch.setattr(uhb_spawn, "_RUNNING", child)
    monkeypatch.setattr(uhb_spawn, "request_shutdown", lambda *a, **k: True)
    monkeypatch.setattr(uhb_spawn, "_wait_closed", lambda timeout=0: seen.append(timeout) or True)
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)

    assert uhb_spawn.stop_running() is True
    assert seen == [uhb_spawn.SHUTDOWN_CLOSE_TIMEOUT], (
        "202 means accepted, not gone: the port has to close before returning"
    )


def test_stop_running_with_the_route_down_and_no_child_is_false(monkeypatch, token) -> None:
    monkeypatch.setattr(uhb_spawn, "_RUNNING", None)
    monkeypatch.setattr(uhb_spawn, "request_shutdown", lambda *a, **k: False)
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)

    assert uhb_spawn.stop_running() is False


def test_the_token_never_appears_in_what_uh_prints(monkeypatch, token, capsys) -> None:
    """A quit that leaks the token into a journal is worse than a failed quit."""
    monkeypatch.setattr(uhb_spawn, "_RUNNING", None)
    monkeypatch.setattr(uhb_spawn, "stop_recorded", lambda *a, **k: False)
    client = _Client(401)

    uhb_spawn.request_shutdown("http://127.0.0.1:8101", client=client)
    uhb_spawn.stop_running()
    captured = capsys.readouterr()

    assert token not in captured.out + captured.err
