"""A provider payload never carries an empty ``tool_calls`` list.

DeepSeek (and gateways with the same strictness) answer
``400 Invalid 'messages[N].tool_calls': empty array`` on the turn AFTER a reply
that called no tool, because the assistant message is replayed from history.
The session keeps its own dicts; only the wire payload is cleaned.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from uh.providers import request_messages
from uh.providers.ollama import OllamaProvider
from uh.providers.omnirouter import OmniRouterProvider
from uh.router.catalog import ProviderSpec
from uh.router.client import chat_completion

URL = "http://127.0.0.1:20128"

HISTORY = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "salut"},
    {"role": "assistant", "content": "bonjour", "tool_calls": []},
]


# --------------------------------------------------------------------------- #
# the helper
# --------------------------------------------------------------------------- #
def test_an_empty_tool_calls_list_is_dropped() -> None:
    cleaned = request_messages(HISTORY)

    assert "tool_calls" not in cleaned[2]
    assert cleaned[0] == HISTORY[0] and cleaned[1] == HISTORY[1]


def test_the_original_messages_are_not_mutated() -> None:
    request_messages(HISTORY)
    assert HISTORY[2]["tool_calls"] == [], "history must keep its own shape"


def test_a_real_tool_call_is_preserved() -> None:
    call = {"function": {"name": "read_file", "arguments": {"path": "a"}}}
    message = {"role": "assistant", "content": "", "tool_calls": [call]}

    assert request_messages([message])[0]["tool_calls"] == [call]


def test_a_missing_tool_calls_key_is_left_alone() -> None:
    assert request_messages([{"role": "user", "content": "hi"}]) == [
        {"role": "user", "content": "hi"}
    ]


# --------------------------------------------------------------------------- #
# every payload builder
# --------------------------------------------------------------------------- #
def _ollama_response() -> SimpleNamespace:
    return SimpleNamespace(
        status_code=200,
        text="",
        json=lambda: {"message": {"content": "bonjour", "tool_calls": []}},
    )


def test_ollama_does_not_send_an_empty_tool_calls(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _ollama_response()

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OllamaProvider("tinyllama:latest", base_url_override="http://127.0.0.1:11434")
    provider.chat(HISTORY, [])

    sent = captured["json"]["messages"]
    assert all("tool_calls" not in message for message in sent)
    assert HISTORY[2]["tool_calls"] == [], "the session history is untouched"


def test_omnirouter_does_not_send_an_empty_tool_calls(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"choices": [{"message": {"content": "bonjour"}}]},
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OmniRouterProvider(model="qwen3:8b", base_url=URL, api_key="k")
    provider.chat(HISTORY, [])

    sent = captured["json"]["messages"]
    assert all("tool_calls" not in message for message in sent)


def test_the_free_router_body_does_not_send_an_empty_tool_calls() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "bonjour", "tool_calls": []}}]}
        )

    spec = ProviderSpec(name="fake", base_url=URL, verified="verified")
    chat_completion(
        spec,
        "key",
        "model-x",
        HISTORY,
        [],
        transport=httpx.MockTransport(handler),
    )

    assert bodies, "the request was never sent"
    assert all("tool_calls" not in message for message in bodies[0]["messages"])
