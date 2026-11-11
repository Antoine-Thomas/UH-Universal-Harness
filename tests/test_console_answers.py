"""Console: answer rendering, including the empty-answer-after-tool-call retry."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import config as uh_config
from uh import console


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider) -> console.ChatSession:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    return console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=4,
    )


class ToolThenText:
    """First turn: a tool call with no text. Second: empty. Third: the answer."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "noop", "arguments": {}}}],
            }
        if self.calls == 2:
            return {"role": "assistant", "content": "", "tool_calls": []}
        return {"role": "assistant", "content": "voici la reponse", "tool_calls": []}


class AlwaysEmptyAfterTool:
    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "noop", "arguments": {}}}],
            }
        return {"role": "assistant", "content": "", "tool_calls": []}


class NoopToolset:
    name = "noop"
    tools = [{"type": "function", "function": {"name": "noop", "description": "", "parameters": {}}}]

    def call(self, name: str, args: dict) -> dict:
        return {"ok": True}


def test_empty_answer_after_tool_call_is_retried_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = ToolThenText()
    session = _session(tmp_path, monkeypatch, provider)
    session.toolsets = {"noop": NoopToolset()}

    assert session.run_text("fais quelque chose") == 0

    captured = capsys.readouterr()
    assert "voici la reponse" in captured.out
    assert "(empty response)" not in captured.out
    assert "asking once more" in captured.err
    assert provider.calls == 3
    # the synthetic nudge never lands in the conversation (the system prompt
    # already contains "without calling a tool", so match the nudge exactly)
    assert not any(
        "Answer the original question now" in str(message.get("content") or "")
        for message in session.history
    )


def test_empty_after_retry_falls_back_to_the_explicit_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    provider = AlwaysEmptyAfterTool()
    session = _session(tmp_path, monkeypatch, provider)
    session.toolsets = {"noop": NoopToolset()}

    assert session.run_text("fais quelque chose") == 0

    captured = capsys.readouterr()
    assert "(empty response)" in captured.out
    assert provider.calls >= 3  # one initial pass + the retry, each with tool calls


def test_plain_empty_answer_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class SilentNoTools:
        model = "stub:1b"

        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages, tools, *, on_token=None):
            self.calls += 1
            return {"role": "assistant", "content": "", "tool_calls": []}

    provider = SilentNoTools()
    session = _session(tmp_path, monkeypatch, provider)

    assert session.run_text("bonjour") == 0
    assert provider.calls == 1  # no tool used: no retry
    assert "(empty response)" in capsys.readouterr().out


def test_streamed_answer_is_not_duplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Streaming:
        model = "stub:1b"

        def chat(self, messages, tools, *, on_token=None):
            if on_token is not None:
                on_token("bon")
                on_token("jour")
            return {"role": "assistant", "content": "bonjour", "tool_calls": []}

    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    session = console.ChatSession(
        provider_factory=lambda kind, model: Streaming(),
        provider_kind="stub",
        chain=[("stub", Streaming())],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=True,
        store=None,
        max_iterations=2,
    )
    assert session.run_text("salut") == 0
    out = capsys.readouterr().out
    assert out.count("bonjour") == 1
