"""uh model pull: NDJSON progress rendering, error handling (no real download)."""

from __future__ import annotations

import json
from typing import List

import pytest

from uh import cli


class FakePullResponse:
    def __init__(self, lines: List[str], status: int = 200) -> None:
        self._lines = lines
        self.status_code = status

    def __enter__(self) -> "FakePullResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def iter_lines(self):
        yield from self._lines

    def iter_text(self):
        yield "\n".join(self._lines)


def _patch_pull(monkeypatch: pytest.MonkeyPatch, lines: List[str], status: int = 200) -> dict:
    captured: dict = {}

    def fake_stream(method, url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return FakePullResponse(lines, status=status)

    monkeypatch.setattr("uh.providers.ollama.httpx.stream", fake_stream)
    return captured


def test_pull_renders_progress_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = [
        json.dumps({"status": "pulling manifest"}),
        json.dumps({"status": "downloading", "completed": 100, "total": 400}),
        json.dumps({"status": "downloading", "completed": 400, "total": 400}),
        json.dumps({"status": "success"}),
    ]
    captured = _patch_pull(monkeypatch, lines)

    code = cli.main(["model", "pull", "qwen2.5:0.5b"])

    out = capsys.readouterr()
    assert code == 0
    assert out.out == ""  # nothing on stdout: the command is scriptable
    assert "pulling manifest" in out.err
    assert "downloading" in out.err
    assert "100%" in out.err
    assert "pulled qwen2.5:0.5b" in out.err
    assert captured["url"].endswith("/api/pull")
    assert captured["payload"] == {"name": "qwen2.5:0.5b", "stream": True}


def test_pull_error_event_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _patch_pull(monkeypatch, [json.dumps({"error": "model not found"})])

    code = cli.main(["model", "pull", "does-not-exist:0b"])

    out = capsys.readouterr()
    assert code == 1
    assert "model not found" in out.err


def test_pull_unreachable_ollama_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import httpx

    def boom(*_args, **_kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("uh.providers.ollama.httpx.stream", boom)

    code = cli.main(["model", "pull", "tiny:0.1b"])

    out = capsys.readouterr()
    assert code == 1
    assert "cannot reach Ollama" in out.err


def test_quiet_pull_only_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = [
        json.dumps({"status": "pulling manifest"}),
        json.dumps({"status": "downloading", "completed": 400, "total": 400}),
        json.dumps({"status": "success"}),
    ]
    _patch_pull(monkeypatch, lines)

    code = cli.main(["model", "pull", "tiny:0.1b", "--quiet"])

    out = capsys.readouterr()
    assert code == 0
    assert "pulling manifest" not in out.err
    assert "downloading" not in out.err
    assert "pulled tiny:0.1b" in out.err


def test_interactive_without_tty_aborts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("no download may start without confirmation")

    monkeypatch.setattr("uh.providers.ollama.httpx.stream", forbidden)

    code = cli.main(["model", "pull", "big:70b", "--interactive"])

    out = capsys.readouterr()
    assert code == 1
    assert "needs a TTY" in out.err
