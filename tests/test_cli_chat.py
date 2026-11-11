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
