"""Console rendering: status bar, plain fallback, NO_COLOR, rich optional."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import config as uh_config
from uh import console


class StubProvider:
    model = "stub:1b"

    def chat(self, messages, tools, *, on_token=None):
        if on_token is not None:
            on_token("bonjour")
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> console.ChatSession:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    return console.ChatSession(
        provider_factory=lambda kind, model: StubProvider(),
        provider_kind="stub",
        chain=[("stub", StubProvider())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
    )


def test_status_line_has_the_five_fields(session: console.ChatSession) -> None:
    line = console.Console.status_line(session)
    parts = [part.strip() for part in line.split("·")]
    assert parts[0] == "stub"
    assert parts[1] == "stub:1b"
    assert parts[2] == "unsaved"
    assert parts[3].endswith("msg")
    assert parts[4].startswith("ctx ~")


def test_no_color_disables_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    stream = io.StringIO()
    stream.isatty = lambda: True  # type: ignore[attr-defined]
    assert console.use_color(stream) is False


def test_colour_needs_a_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    plain = io.StringIO()
    assert console.use_color(plain) is False

    tty = io.StringIO()
    tty.isatty = lambda: True  # type: ignore[attr-defined]
    assert console.use_color(tty) is True


def test_plain_table_without_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(console, "rich_available", lambda: False)
    ui = console.Console(stream=io.StringIO())
    ui.table(["NAME", "SIZE"], [["a", "1"], ["bb", "22"]])
    output = ui.stream.getvalue()
    assert "NAME" in output and "SIZE" in output
    assert "bb" in output


def test_rich_absent_still_prints_status(
    session: console.ChatSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(console, "rich_available", lambda: False)
    ui = console.Console()
    ui.show_status(session)
    captured = capsys.readouterr()
    assert "stub:1b" in captured.err


def test_run_text_persists_when_a_store_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from uh.store import Store

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "home" / "uh.db")
    session = console.ChatSession(
        provider_factory=lambda kind, model: StubProvider(),
        provider_kind="stub",
        chain=[("stub", StubProvider())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=store,
        max_iterations=2,
    )

    assert session.run_text("salut") == 0
    captured = capsys.readouterr()
    assert "bonjour" in captured.out
    assert "session " in captured.err
    assert session.session_id is not None
    assert store.message_count(session.session_id) == 2
    store.close()


def test_empty_answer_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Silent:
        model = "stub:1b"

        def chat(self, messages, tools, *, on_token=None):
            return {"role": "assistant", "content": "", "tool_calls": []}

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    session = console.ChatSession(
        provider_factory=lambda kind, model: Silent(),
        provider_kind="stub",
        chain=[("stub", Silent())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
    )
    session.run_text("sans reponse")
    assert "(empty response)" in capsys.readouterr().out


class InterruptOnce:
    """Streams one token, then behaves like Ctrl+C in httpx; answers afterwards."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.calls == 1:
            if on_token is not None:
                on_token("demi-reponse")
            raise KeyboardInterrupt()
        return {"role": "assistant", "content": "seconde reponse", "tool_calls": []}


def _streaming_session(tmp_path: Path, provider) -> console.ChatSession:
    return console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=True,
        store=None,
        max_iterations=2,
    )


def test_ctrl_c_during_a_streamed_turn_keeps_the_session_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    provider = InterruptOnce()
    session = _streaming_session(tmp_path, provider)

    code = session.run_text("premier")

    captured = capsys.readouterr()
    assert code == console.INTERRUPT_EXIT
    assert "[interrompu]" in captured.err
    # the half answer is closed on its own line, then acknowledged on stderr
    assert captured.out.endswith("\n")
    # nothing from the cut turn is kept: only the system prompt remains
    assert [m["role"] for m in session.history] == ["system"]

    assert session.run_text("second") == 0
    assert "seconde reponse" in capsys.readouterr().out


def test_ctrl_c_is_not_a_traceback_even_in_once_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`uh chat --once` goes through run_text too: no KeyboardInterrupt escapes it."""
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    provider = InterruptOnce()
    session = _streaming_session(tmp_path, provider)

    assert session.run_text("premier") == console.INTERRUPT_EXIT
    assert "Traceback" not in capsys.readouterr().err
