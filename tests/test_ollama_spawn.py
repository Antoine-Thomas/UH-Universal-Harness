"""Ollama is started if it exists, and the pass never depends on it.

The rule these tests defend: UH does not install, bundle or require Ollama. A
machine without it runs the same pass on the cloud free tier, and the router
chain is what decides - not this module.
"""

from __future__ import annotations

import httpx
import pytest

from uh import ollama_spawn


@pytest.fixture(autouse=True)
def _no_ambient_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ollama_spawn.BINARY_ENV, raising=False)


def test_already_up_when_the_api_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ollama_spawn, "is_up", lambda *a, **k: True)
    monkeypatch.setattr(
        ollama_spawn, "find_binary", lambda: pytest.fail("a running server needs no binary")
    )

    assert ollama_spawn.ensure() == ollama_spawn.ALREADY_UP


def test_not_installed_when_the_binary_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ollama_spawn, "is_up", lambda *a, **k: False)
    monkeypatch.setattr(ollama_spawn, "find_binary", lambda: None)

    assert ollama_spawn.ensure() == ollama_spawn.NOT_INSTALLED


def test_started_when_the_binary_exists_and_the_api_comes_up(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "ollama.exe"
    binary.write_bytes(b"")
    launched: list = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            launched.append(argv)

    calls = {"n": 0}

    def fake_up(*_a, **_k):
        calls["n"] += 1
        return calls["n"] > 1  # dead before the launch, up after

    monkeypatch.setattr(ollama_spawn, "find_binary", lambda: binary)
    monkeypatch.setattr(ollama_spawn, "is_up", fake_up)
    monkeypatch.setattr(ollama_spawn.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(ollama_spawn.time, "sleep", lambda _s: None)

    assert ollama_spawn.ensure(timeout=5) == ollama_spawn.STARTED
    assert launched and launched[0][0] == str(binary) and launched[0][1] == "serve"


def test_a_binary_that_never_answers_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server that starts but never answers must not hang the pass."""
    binary = pytest.importorskip("pathlib").Path(__file__)
    monkeypatch.setattr(ollama_spawn, "find_binary", lambda: binary)
    monkeypatch.setattr(ollama_spawn, "is_up", lambda *a, **k: False)
    monkeypatch.setattr(ollama_spawn.subprocess, "Popen", lambda argv, **kw: None)
    monkeypatch.setattr(ollama_spawn.time, "sleep", lambda _s: None)

    assert ollama_spawn.ensure(timeout=1) == ollama_spawn.NOT_INSTALLED


def test_the_override_env_is_authoritative(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope" / "ollama.exe"
    monkeypatch.setenv(ollama_spawn.BINARY_ENV, str(missing))

    assert ollama_spawn.find_binary() is None, "a path that does not exist means 'not installed'"


def test_the_override_env_wins_when_it_exists(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "portable" / "ollama"
    binary.parent.mkdir()
    binary.write_bytes(b"")
    monkeypatch.setenv(ollama_spawn.BINARY_ENV, str(binary))

    assert ollama_spawn.find_binary() == binary


def test_the_probe_is_a_short_get(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"models": []})

    original = httpx.Client
    monkeypatch.setattr(
        ollama_spawn.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler))
    )

    assert ollama_spawn.is_up("http://127.0.0.1:11434") is True
    assert seen == ["http://127.0.0.1:11434/api/tags"]


def test_the_probe_survives_a_refused_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    original = httpx.Client
    monkeypatch.setattr(
        ollama_spawn.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler))
    )

    assert ollama_spawn.is_up() is False


def test_the_scheduled_command_asks_for_ollama() -> None:
    from uh import dream_schedule

    arguments = dream_schedule.task_arguments(1000)

    assert "--start-ollama" in arguments
    assert "--start-uhb" in arguments


def test_without_the_flag_no_ollama_is_started(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default behaviour does not change: no flag, no attempt."""
    from uh import cli, dream_pass

    monkeypatch.setenv("UH_HOME", str(tmp_path / "uh-home"))
    monkeypatch.setattr(ollama_spawn, "ensure", lambda *a, **k: pytest.fail("not asked, not started"))

    class Report:
        status = "ok"
        reason = ""
        ideas: list = []
        artifacts: list = []
        classification: dict = {}
        notes: list = []
        tokens = {"prompt": 0, "completion": 0, "total": 0}
        wall_time_s = 0.0

        def render(self) -> str:
            return "# pass"

    monkeypatch.setattr(dream_pass, "DreamScheduler", lambda **kw: type(
        "S", (), {"run_once": lambda self, dry_run=False: Report()}
    )())

    assert cli.main(["dream", "--now", "--no-write"]) == 0
