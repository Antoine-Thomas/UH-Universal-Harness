"""uh chat CLI: one-shot mode with a stubbed provider (no network, no real model)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import cli
from uh.agent import BudgetExhausted
from uh.providers import ProviderError


class StubProvider:
    """Answers with a fixed text; optionally raises instead."""

    model = "stub:1b"

    def __init__(self, answer: str = "bonjour a toi", error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.seen: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.seen.append(messages)
        if self.error is not None:
            raise self.error
        return {"role": "assistant", "content": self.answer, "tool_calls": []}


def test_once_prints_answer_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stub = StubProvider()
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: stub)
    monkeypatch.setattr("sys.stdin", io.StringIO("dis bonjour\n"))
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    code = cli.main(["chat", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "bonjour a toi" in captured.out
    assert stub.seen, "the provider was never called"


def test_once_provider_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stub = StubProvider(error=ProviderError("cannot reach Ollama"))
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: stub)
    monkeypatch.setattr("sys.stdin", io.StringIO("dis bonjour\n"))
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    code = cli.main(["chat", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 1
    assert "provider error" in captured.err


def test_once_budget_exhausted_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stub = StubProvider(error=BudgetExhausted("no final answer after 1 provider call(s)"))
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: stub)
    monkeypatch.setattr("sys.stdin", io.StringIO("boucle\n"))
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    code = cli.main(["chat", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 1
    assert "max_iterations" in captured.err or "no final answer" in captured.err


def test_unknown_provider_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["chat", "--once", "--provider", "openai", "--model", "x"])
    assert code == 1
    message = capsys.readouterr().err
    assert "unknown provider" in message
    assert "available:" in message


def test_empty_stdin_exits_zero_without_calling_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stub = StubProvider()
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: stub)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    code = cli.main(["chat", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)])
    assert code == 0
    assert not stub.seen


class StreamInterrupt:
    """One streamed token then Ctrl+C, then a normal answer on the next turn."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.calls == 1:
            if on_token is not None:
                on_token("demi")
            raise KeyboardInterrupt()
        return {"role": "assistant", "content": "reponse du second tour", "tool_calls": []}


def _repl_session(tmp_path: Path, provider, toolsets: dict | None = None) -> object:
    from uh import config as uh_config
    from uh import console as uh_console

    return uh_console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets=toolsets or {},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=True,
        store=None,
        max_iterations=2,
    )


def test_repl_prompts_again_after_ctrl_c_during_a_turn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    provider = StreamInterrupt()
    session = _repl_session(tmp_path, provider)
    answers = iter(["premier", "second", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    code = cli._run_repl(session)

    captured = capsys.readouterr()
    assert code == 0
    assert "[interrompu]" in captured.err
    assert "Traceback" not in captured.err
    # the REPL asked a second question and the model answered it
    assert provider.calls == 2
    assert "reponse du second tour" in captured.out


def test_repl_survives_an_interrupt_outside_run_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Ctrl+C inside a slash command must not kill the loop either."""
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    session = _repl_session(tmp_path, StreamInterrupt())
    answers = iter(["premier", "/exit"])

    def boom(_text: str) -> int:
        raise KeyboardInterrupt()

    monkeypatch.setattr(session, "run_text", boom)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert cli._run_repl(session) == 0
    assert "[interrompu]" in capsys.readouterr().err


def test_repl_prints_the_full_logo_and_a_readable_header(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import os as _os

    from uh import banner

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(banner, "should_show", lambda: True)
    monkeypatch.setattr(
        banner.shutil, "get_terminal_size", lambda *a, **kw: _os.terminal_size((100, 30))
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": "/exit")

    assert cli._run_repl(_repl_session(tmp_path, StubProvider())) == 0

    captured = capsys.readouterr()
    assert "█" in captured.out, "the console must show the logo, not the short banner"
    assert "provider-agnostic | local-first" not in captured.err
    assert "provider stub · model stub:1b" in captured.err
    assert "workspace" in captured.err
    assert "unsaved" in captured.err


def test_repl_leaves_a_blank_line_before_the_prompt_and_the_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    answers = iter(["salut", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert cli._run_repl(_repl_session(tmp_path, StubProvider())) == 0

    out = capsys.readouterr().out
    assert out.startswith("\n"), "a blank line precedes the first prompt"
    assert "\n\n" in out, "the question and the answer are separated"
    assert "bonjour a toi" in out


@pytest.mark.parametrize(
    "line",
    [
        "uh doctor",
        "uh upgrade",
        "pip install uh-harness",
        "git status",
        "ls -la",
        "cd ..",
        "ollama serve",
        "docker ps",
        "UH DOCTOR",
    ],
)
def test_shell_lines_are_recognised(line: str) -> None:
    assert cli.shell_command_hint(line) == cli.SHELL_WARNING


@pytest.mark.parametrize(
    "line",
    [
        "bonjour",
        "/help",
        "comment ça va ?",
        "explique-moi git",
        "",
        "   ",
    ],
)
def test_prose_and_slash_commands_are_not_shell(line: str) -> None:
    assert cli.shell_command_hint(line) is None


def test_the_warning_says_what_to_do() -> None:
    assert "Type /exit first" in cli.SHELL_WARNING
    assert "prefix with /" in cli.SHELL_WARNING


def test_repl_refuses_a_shell_line_without_calling_the_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    stub = StubProvider()
    session = _repl_session(tmp_path, stub)
    answers = iter(["uh doctor", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    assert cli._run_repl(session) == 0

    captured = capsys.readouterr()
    assert stub.seen == [], "a shell line must never reach the model"
    assert "that looks like a shell command" in captured.err
    assert [m["role"] for m in session.history] == ["system"], "nothing was appended"


def test_the_repl_shows_three_explicit_header_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from uh import __version__ as running

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("UH_QUIET", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/exit")

    assert cli._run_repl(_repl_session(tmp_path, StubProvider())) == 0

    err = capsys.readouterr().err
    assert "Web UI: run 'uh web'" in err
    assert f"Update: run 'uh upgrade' (current: v{running})" in err
    assert "Session: /new for a new session · /help for commands" in err
    assert "Tip:" not in err, "the old single-line tip is gone"


def test_the_header_reads_its_version_from_the_package() -> None:
    """Not from installed metadata: a checkout and its wheel disagree."""
    from uh import __version__

    assert any(__version__ in line for line in cli.startup_header())
    assert any("v9.8.7" in line for line in cli.startup_header("9.8.7"))


def test_the_header_lines_are_ordered_ui_update_session() -> None:
    lines = cli.startup_header()
    assert len(lines) == 3
    assert lines[0].startswith("Web UI:")
    assert lines[1].startswith("Update:")
    assert lines[2].startswith("Session:")


class _UhbStatus:
    """Stands in for Uhbtoolset: one status() call, no HTTP."""

    name = "uhb"
    tools: list = []

    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = 0

    def status(self):
        self.calls += 1
        return self.payload


def test_the_header_shows_uhb_when_the_toolset_was_built(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("UH_QUIET", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/exit")
    uhb = _UhbStatus(
        {"connected": True, "version": "0.4.1", "mode": "dev", "providers": ["a", "b"]}
    )

    assert cli._run_repl(_repl_session(tmp_path, StubProvider(), {"uhb": uhb})) == 0

    err = capsys.readouterr().err
    assert "UHB: connected (v0.4.1) · mode: dev · 2 providers" in err
    assert uhb.calls == 1


def test_the_header_has_no_uhb_line_without_the_toolset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A missing UHB is a normal state: no line, and nothing on stderr about it."""
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("UH_QUIET", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "/exit")

    assert cli._run_repl(_repl_session(tmp_path, StubProvider())) == 0

    assert "UHB" not in capsys.readouterr().err


@pytest.mark.parametrize("value", ["1", "true", "yes", "TRUE"])
def test_uh_quiet_is_recognised(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("UH_QUIET", value)
    assert cli.quiet_mode() is True


@pytest.mark.parametrize("value", ["", "0", "no", "false"])
def test_uh_quiet_is_not_triggered_by_anything_else(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("UH_QUIET", value)
    assert cli.quiet_mode() is False


def test_uh_quiet_silences_the_banner_and_header(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UH_QUIET", "1")
    answers = iter(["salut", "/exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    uhb = _UhbStatus({"connected": True, "version": "0.4.1", "mode": "dev", "providers": []})

    assert cli._run_repl(_repl_session(tmp_path, StubProvider(), {"uhb": uhb})) == 0

    captured = capsys.readouterr()
    assert "Web UI:" not in captured.err
    assert "Update:" not in captured.err
    assert "Session:" not in captured.err
    assert "UHB:" not in captured.err
    assert "provider stub" not in captured.err
    assert "workspace" not in captured.err
    assert "█" not in captured.out
    # the session itself is untouched: the answer still comes through
    assert "bonjour a toi" in captured.out
