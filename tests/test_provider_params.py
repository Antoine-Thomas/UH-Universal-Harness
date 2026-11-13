"""The wire payload: which settings a provider actually receives.

No network - ``httpx.post`` is replaced. These tests are the contract the panel
relies on: the supported keys travel, ``reasoning_effort`` travels only for the
models that document it, and an empty parameter map changes nothing.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import pytest

from uh.providers.ollama import OllamaProvider, chat_options
from uh.providers.omnirouter import OmniRouterProvider, openai_params


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def capture(monkeypatch: pytest.MonkeyPatch, payload: dict) -> List[Dict[str, Any]]:
    """Record every JSON body httpx.post is given."""
    seen: List[Dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        seen.append(kwargs.get("json") or {})
        return FakeResponse(payload)

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


# --------------------------------------------------------------------------- #
# ollama
# --------------------------------------------------------------------------- #
def test_ollama_maps_max_tokens_to_num_predict() -> None:
    options = chat_options({"max_tokens": 512, "temperature": 0.2, "reasoning_effort": "high"})

    assert options == {"temperature": 0.2, "num_predict": 512}


def test_ollama_sends_its_options_and_nothing_unsupported(monkeypatch) -> None:
    seen = capture(
        monkeypatch,
        {"message": {"role": "assistant", "content": "ok"}, "prompt_eval_count": 1, "eval_count": 1},
    )
    provider = OllamaProvider(model="qwen3:8b")
    provider.set_params({"temperature": 0.2, "top_p": 0.9, "max_tokens": 256, "reasoning_effort": "high"})

    provider.chat([{"role": "user", "content": "salut"}], [])

    payload = seen[0]
    assert payload["options"] == {"temperature": 0.2, "top_p": 0.9, "num_predict": 256}
    assert "reasoning_effort" not in json.dumps(payload)


def test_ollama_without_params_sends_no_options(monkeypatch) -> None:
    seen = capture(
        monkeypatch,
        {"message": {"role": "assistant", "content": "ok"}, "prompt_eval_count": 1, "eval_count": 1},
    )
    provider = OllamaProvider(model="qwen3:8b")

    provider.chat([{"role": "user", "content": "salut"}], [])

    assert "options" not in seen[0]


# --------------------------------------------------------------------------- #
# openai-compatible
# --------------------------------------------------------------------------- #
def test_openai_params_keep_the_documented_keys() -> None:
    clean = openai_params(
        {
            "temperature": 0.2,
            "top_p": 0.9,
            "max_tokens": 512,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.2,
            "web_search": False,
        },
        "deepseek-flash",
    )

    assert clean == {
        "temperature": 0.2,
        "top_p": 0.9,
        "max_tokens": 512,
        "frequency_penalty": 0.1,
        "presence_penalty": 0.2,
    }


def test_reasoning_effort_is_only_sent_to_the_models_that_accept_it() -> None:
    assert "reasoning_effort" not in openai_params({"reasoning_effort": "high"}, "deepseek-flash")
    assert openai_params({"reasoning_effort": "high"}, "o3-mini") == {"reasoning_effort": "high"}


def test_the_openai_payload_carries_the_parameters(monkeypatch) -> None:
    seen = capture(
        monkeypatch,
        {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]},
    )
    provider = OmniRouterProvider(
        model="deepseek-flash", base_url="http://127.0.0.1:20128", api_key="test-key"
    )
    provider.set_params({"temperature": 0.3, "max_tokens": 128, "reasoning_effort": "high"})

    provider.chat([{"role": "user", "content": "salut"}], [])

    payload = seen[0]
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 128
    assert "reasoning_effort" not in payload, "deepseek-flash does not document it"
