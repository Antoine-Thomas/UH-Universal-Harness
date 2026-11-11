"""Streaming contract: provider deltas, graceful degradation, NDJSON parsing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from uh import agent
from uh.agent import run_turn
from uh.providers.ollama import OllamaProvider
from uh.toolsets.file import FileToolset


@pytest.fixture(autouse=True)
def _clear_streaming_warnings():
    agent._WARNED_NO_STREAMING.clear()
    yield
    agent._WARNED_NO_STREAMING.clear()


class StreamingProvider:
    """Emits three deltas through on_token, then returns the full message."""

    def __init__(self, deltas: List[str]) -> None:
        self.deltas = deltas
        self.calls = 0

    def chat(
        self,
        messages: List[dict],
        tools: List[dict],
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> dict:
        self.calls += 1
        if on_token is not None:
            for delta in self.deltas:
                on_token(delta)
        return {"role": "assistant", "content": "".join(self.deltas), "tool_calls": []}


class BlockingOnlyProvider:
    """No on_token, no **kwargs: run_turn must degrade and warn exactly once."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: List[dict], tools: List[dict]) -> dict:
        self.calls += 1
        return {"role": "assistant", "content": "blocking answer", "tool_calls": []}


def test_deltas_reach_on_token(tmp_path: Path) -> None:
    provider = StreamingProvider(["Hello", ", ", "world"])
    received: List[str] = []

    history = run_turn(
        [{"role": "user", "content": "hi"}],
        provider,
        {"file": FileToolset(workspace=tmp_path)},
        on_token=received.append,
    )

    assert received == ["Hello", ", ", "world"]
    assert history[-1]["content"] == "Hello, world"


def test_provider_without_on_token_degrades_and_warns_once(tmp_path: Path, capsys) -> None:
    provider = BlockingOnlyProvider()
    toolsets = {"file": FileToolset(workspace=tmp_path)}

    first = run_turn(
        [{"role": "user", "content": "one"}], provider, toolsets, on_token=lambda _t: None
    )
    second = run_turn(
        [{"role": "user", "content": "two"}], provider, toolsets, on_token=lambda _t: None
    )

    assert first[-1]["content"] == "blocking answer"
    assert second[-1]["content"] == "blocking answer"
    assert provider.calls == 2
    captured = capsys.readouterr()
    assert captured.err.count("does not support streaming") == 1


def test_no_on_token_means_blocking_provider_call(tmp_path: Path) -> None:
    provider = StreamingProvider(["x"])

    run_turn([{"role": "user", "content": "hi"}], provider, {"file": FileToolset(workspace=tmp_path)})

    assert provider.calls == 1


class FakeResponse:
    def __init__(self, lines: List[str], status: int = 200) -> None:
        self._lines = lines
        self.status_code = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def iter_lines(self):
        yield from self._lines

    def iter_text(self):
        yield "\n".join(self._lines)

    def json(self):
        return json.loads(self._lines[0])


def _patch_stream(monkeypatch: pytest.MonkeyPatch, lines: List[str], captured: dict) -> None:
    def fake_stream(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = kwargs.get("json")
        return FakeResponse(lines)

    monkeypatch.setattr("uh.providers.ollama.httpx.stream", fake_stream)


def test_ollama_streaming_parses_ndjson(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "Bon"} , "done": False}),
        "",
        json.dumps({"message": {"content": "jour"}, "done": False}),
        json.dumps({"message": {"content": " !"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]
    captured: dict = {}
    _patch_stream(monkeypatch, lines, captured)

    provider = OllamaProvider("qwen3:8b")
    received: List[str] = []
    message = provider.chat(
        [{"role": "user", "content": "salut"}], [], on_token=received.append
    )

    assert received == ["Bon", "jour", " !"]
    assert message == {"role": "assistant", "content": "Bonjour !", "tool_calls": []}
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/chat")
    assert captured["payload"]["stream"] is True


def test_ollama_blocking_request_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    class FakePost:
        status_code = 200

        def json(self):
            return {"message": {"role": "assistant", "content": "ok", "tool_calls": []}}

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["payload"] = kwargs.get("json")
        return FakePost()

    monkeypatch.setattr("uh.providers.ollama.httpx.post", fake_post)

    message = OllamaProvider("qwen3:8b").chat([{"role": "user", "content": "x"}], [])

    assert message["content"] == "ok"
    assert seen["payload"]["stream"] is False


def test_ollama_streaming_extracts_final_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    lines = [
        json.dumps({"message": {"content": "je regarde"}, "done": False}),
        json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "a.txt"},
                            }
                        }
                    ],
                },
                "done": True,
            }
        ),
    ]
    captured: dict = {}
    _patch_stream(monkeypatch, lines, captured)

    provider = OllamaProvider("qwen3:8b")
    message = provider.chat([{"role": "user", "content": "lis a.txt"}], [], on_token=lambda _t: None)

    assert message["content"] == "je regarde"
    assert message["tool_calls"][0]["function"]["name"] == "read_file"
    assert message["tool_calls"][0]["function"]["arguments"] == {"path": "a.txt"}


def test_ollama_streaming_error_event_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    _patch_stream(monkeypatch, [json.dumps({"error": "model not found"})], captured)

    from uh.providers import ProviderError

    with pytest.raises(ProviderError):
        OllamaProvider("nope").chat([{"role": "user", "content": "x"}], [], on_token=lambda _t: None)
