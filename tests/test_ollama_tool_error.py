"""Ollama's "does not support tools" 400 becomes an instruction, not a JSON dump.

tinyllama (and any model without tool calling) rejects the ``tools`` array with
a 400 whose body is a raw registry message. The fallback must still happen, but
the user has to read something actionable - in the exception and in the
``fallback:`` line the console prints.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from uh import config as uh_config
from uh import console
from uh.providers import ProviderError
from uh.providers.ollama import OllamaProvider

URL = "http://127.0.0.1:11434"
BODY = json.dumps(
    {
        "error": {
            "message": (
                "registry.ollama.ai/library/tinyllama:latest does not support tools"
            )
        }
    }
)
CLEAN = (
    "model 'tinyllama:latest' does not support tool calling; "
    "try a different model or disable tools."
)
TOOLS = [{"type": "function", "function": {"name": "read_file"}}]


def _response(status: int = 400, text: str = BODY):
    return type(
        "R", (), {"status_code": status, "text": text, "json": lambda self: json.loads(text)}
    )()


class _Stream:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_text(self):
        return iter([self._text])

    def iter_lines(self):
        return iter([self._text])


def test_a_blocking_call_raises_the_clean_message(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _response())

    provider = OllamaProvider("tinyllama:latest", base_url_override=URL)
    with pytest.raises(ProviderError) as caught:
        provider.chat([{"role": "user", "content": "salut"}], TOOLS)

    assert str(caught.value) == CLEAN
    assert caught.value.status == 400
    assert "does not support tools" not in str(caught.value)


def test_a_streamed_call_raises_the_clean_message(monkeypatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _Stream(400, BODY))

    provider = OllamaProvider("tinyllama:latest", base_url_override=URL)
    with pytest.raises(ProviderError) as caught:
        provider.chat([{"role": "user", "content": "salut"}], TOOLS, on_token=lambda _t: None)

    assert str(caught.value) == CLEAN
    assert caught.value.status == 400


def test_the_classified_reason_carries_the_hint() -> None:
    error = OllamaProvider("tinyllama:latest", base_url_override=URL)._http_error(400, BODY)

    assert error.reason == (
        "400 error: model 'tinyllama:latest' does not support tool calling"
    )


def test_another_400_keeps_its_body() -> None:
    error = OllamaProvider("tinyllama:latest", base_url_override=URL)._http_error(
        400, '{"error": {"message": "invalid options"}}'
    )

    assert error.status == 400
    assert error.hint == ""
    assert "invalid options" in str(error)
    assert error.reason == "400 error"


def test_the_fallback_line_explains_itself(monkeypatch, tmp_path: Path, capsys) -> None:
    class Answering:
        model = "deepseek-v4-flash"

        def chat(self, messages, tools, *, on_token=None):
            return {"role": "assistant", "content": "bonjour", "tool_calls": []}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _response())
    ollama = OllamaProvider("tinyllama:latest", base_url_override=URL)
    session = console.ChatSession(
        provider_factory=lambda kind, model: Answering(),
        provider_kind="ollama",
        chain=[("ollama", ollama), ("deepseek", Answering())],
        model="tinyllama:latest",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
    )

    assert session.run_text("salut") == 0

    err = capsys.readouterr().err
    assert (
        "fallback: ollama -> deepseek "
        "(reason: 400 error: model 'tinyllama:latest' does not support tool calling)"
    ) in err
    assert "registry.ollama.ai" not in err, "the raw registry message must not be printed"
