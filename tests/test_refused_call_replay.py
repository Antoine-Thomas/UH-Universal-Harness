"""A refused or withdrawn tool call must never make the next API turn a 422.

Measured 2026-11-20 on DeepSeek: ``HTTP 422: messages[N]: missing field 'type'``
after a tool call was refused or removed from a session. The gateway below is
as strict as DeepSeek: every replayed call needs ``type: "function"`` and a
string ``arguments``, every ``tool`` message needs the ``tool_call_id`` of a call
that precedes it, and a call to a tool absent from ``request.tools`` is refused.
"""

from __future__ import annotations

import json

from uh.agent import run_turn
from uh.providers import request_messages


def strict_gateway_errors(payload: dict) -> list[str]:
    declared = {(tool.get("function") or {}).get("name") for tool in payload.get("tools") or []}
    errors: list[str] = []
    seen: set[str] = set()
    for index, message in enumerate(payload["messages"]):
        if message.get("role") == "assistant" and "tool_calls" in message:
            if not message["tool_calls"]:
                errors.append(f"messages[{index}]: empty tool_calls")
            for call in message["tool_calls"]:
                if call.get("type") != "function":
                    errors.append(f"messages[{index}]: missing field 'type'")
                if not isinstance(call.get("function", {}).get("arguments"), str):
                    errors.append(f"messages[{index}]: arguments is not a string")
                if call["function"]["name"] not in declared:
                    errors.append(f"messages[{index}]: tool not in request.tools")
                seen.add(call.get("id", ""))
        if message.get("role") == "tool" and message.get("tool_call_id") not in seen:
            errors.append(f"messages[{index}]: tool answer without its call")
    return errors


class StrictProvider:
    """Scripted model that runs the real wire boundary and checks it like a gateway."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.errors: list[str] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {"messages": request_messages(messages, tools=tools)}
        if tools:
            payload["tools"] = tools
        self.errors += strict_gateway_errors(payload)
        return self.replies.pop(0)


def ollama_style_call(name: str, call_id: str = "c1") -> dict:
    # No ``type``, arguments as an object: what a local model stores in the session.
    return {"id": call_id, "function": {"index": 0, "name": name, "arguments": {}}}


def test_a_refused_call_then_a_second_turn_is_accepted_by_a_strict_gateway() -> None:
    provider = StrictProvider(
        [
            {"role": "assistant", "content": "", "tool_calls": [ollama_style_call("execute_code")]},
            {"role": "assistant", "content": "je ne peux pas l'exécuter ici"},
            {"role": "assistant", "content": "voici le code"},
        ]
    )
    history = [{"role": "user", "content": "trace un graphique"}]
    # Turn 1: no toolset offers `execute_code`, so the call is refused.
    history = run_turn(history, provider, {})
    refusal = [m for m in history if m.get("role") == "tool"]
    assert refusal and json.loads(refusal[0]["content"])["type"] == "forbidden"
    assert history[-1]["content"] == "je ne peux pas l'exécuter ici"
    # Turn 2 replays the refused call from the session.
    history.append({"role": "user", "content": "et maintenant ?"})
    history = run_turn(history, provider, {})
    assert history[-1]["content"] == "voici le code"
    assert provider.errors == []


def test_a_turn_that_offers_no_tool_does_not_replay_an_earlier_call() -> None:
    """Chat mode after dev mode: `tools=[]` is an answer, the old calls go."""
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [ollama_style_call("execute_code")]},
        {"role": "tool", "name": "execute_code", "tool_call_id": "c1", "content": "{}"},
        {"role": "user", "content": "suite"},
    ]
    sent = request_messages(history, tools=[])
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert "tool_calls" not in sent[1]
    assert sent[1]["content"] == "[résultat de `execute_code` : {}]", (
        "what it produced stays readable"
    )
    assert strict_gateway_errors({"messages": sent}) == []


def test_a_call_without_tool_list_keeps_its_type_and_string_arguments() -> None:
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [ollama_style_call("patch")]},
        {"role": "tool", "name": "patch", "tool_call_id": "c1", "content": "{}"},
    ]
    sent = request_messages(history)
    assert sent[1]["tool_calls"][0]["type"] == "function"
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert (
        strict_gateway_errors({"messages": sent, "tools": [{"function": {"name": "patch"}}]}) == []
    )
