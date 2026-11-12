"""Slash-command registry: resolution, aliases, suggestions, handlers."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from uh import commands
from uh import config as uh_config
from uh.console import ChatSession
from uh.toolsets import build_toolsets


class StubProvider:
    def __init__(self, model: str = "stub:1b", answer: str = "ok") -> None:
        self.model = model
        self.answer = answer

    def chat(self, messages, tools, *, on_token=None):
        if on_token is not None:
            on_token(self.answer)
        return {"role": "assistant", "content": self.answer, "tool_calls": []}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ChatSession:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    provider = StubProvider()
    return ChatSession(
        provider_factory=lambda kind, model: StubProvider(model=model or "stub:1b"),
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets=build_toolsets(tmp_path),
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
    )


MANDATORY = {
    "help", "model", "provider", "tools", "clear", "new",
    "sessions", "resume", "config", "doctor", "exit",
}


def test_mandatory_commands_are_registered() -> None:
    assert MANDATORY <= set(commands.REGISTRY_BY_NAME)


def test_every_entry_has_a_handler_and_help() -> None:
    for command in commands.REGISTRY:
        assert callable(command.handler)
        assert command.help
        assert command.name == command.name.lower()


def test_aliases_resolve() -> None:
    assert commands.resolve("h") is commands.REGISTRY_BY_NAME["help"]
    assert commands.resolve("/q") is commands.REGISTRY_BY_NAME["exit"]
    assert commands.resolve("reset") is commands.REGISTRY_BY_NAME["new"]
    assert commands.resolve("nope") is None


def test_unknown_command_suggests_the_closest(session: ChatSession) -> None:
    result = commands.dispatch(session, "/modl")
    assert result.err.startswith("unknown command /modl")
    assert "/model" in result.err
    assert result.out == ""


def test_is_command_distinguishes_prompts() -> None:
    assert commands.is_command("/help")
    assert not commands.is_command("explique-moi /help")
    assert not commands.is_command("")


def test_help_lists_groups_and_commands(session: ChatSession) -> None:
    result = commands.dispatch(session, "/help")
    for fragment in ("commands:", "Session", "Model", "Info", "/help", "/resume <id>"):
        assert fragment in result.out


def test_help_on_one_command(session: ChatSession) -> None:
    assert "/model" in commands.dispatch(session, "/help model").out
    assert "unknown command" in commands.dispatch(session, "/help nope").err


def test_version_and_status(session: ChatSession) -> None:
    from uh import __version__

    assert commands.dispatch(session, "/version").out == f"uh {__version__}"
    status = commands.dispatch(session, "/status").out
    assert "provider: stub" in status
    assert "model:    stub:1b" in status
    assert "context:" in status


def test_clear_and_new_reset_history(session: ChatSession) -> None:
    session.run_text("bonjour")
    assert len(session.history) == 3
    assert "cleared" in commands.dispatch(session, "/clear").err
    assert len(session.history) == 1


def test_model_switch_uses_the_factory(session: ChatSession) -> None:
    result = commands.dispatch(session, "/model autre:3b")
    assert "model set to autre:3b" in result.err
    assert session.model == "autre:3b"
    assert session.provider.model == "autre:3b"


def test_tools_lists_every_toolset(session: ChatSession) -> None:
    out = commands.dispatch(session, "/tools").out
    assert "file:" in out and "terminal:" in out and "memory:" in out


def test_resume_and_title_need_a_store(session: ChatSession) -> None:
    assert "disabled" in commands.dispatch(session, "/resume abc").err
    assert "no stored session" in commands.dispatch(session, "/title x").err
    assert "usage: /resume" in commands.dispatch(session, "/resume").err


def test_exit_requests_termination(session: ChatSession) -> None:
    assert commands.dispatch(session, "/exit").exit is True
    assert commands.dispatch(session, "/quit").exit is True


def test_retry_without_history_reports_it(session: ChatSession) -> None:
    assert "nothing to retry" in commands.dispatch(session, "/retry").err


def test_history_shows_messages(session: ChatSession) -> None:
    session.run_text("bonjour le monde")
    out = commands.dispatch(session, "/history").out
    assert "user: bonjour le monde" in out
    assert "assistant: ok" in out


def test_completion_candidates() -> None:
    assert "model" in commands.completion_candidates("mo")
    assert commands.completion_candidates("zzz") == []


SAMPLE_SEARCH_HTML = """
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=abc">Example Page</a>
  <a class="result__snippet">An example snippet.</a>
</div>
</body></html>
"""


class _FakeSearchResponse:
    text = SAMPLE_SEARCH_HTML
    status_code = 200

    def raise_for_status(self) -> None:
        return None


def test_web_runs_the_search_without_the_model(
    session: ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeSearchResponse())
    before = list(session.history)
    result = commands.dispatch(session, "/web une requête")
    assert "Example Page" in result.out
    assert "https://example.com/page" in result.out
    assert "An example snippet." in result.out
    assert session.history == before  # no model call, nothing added to the history


def test_web_requires_a_query(session: ChatSession) -> None:
    assert "usage: /web" in commands.dispatch(session, "/web").err


def test_web_reports_search_errors(
    session: ChatSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args, **kwargs):
        raise httpx.ConnectError("dns failed")

    monkeypatch.setattr(httpx, "get", broken)
    result = commands.dispatch(session, "/web test")
    assert result.err.startswith("web: search failed")
