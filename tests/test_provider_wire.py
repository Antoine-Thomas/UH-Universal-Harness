"""The provider boundary: two wire shapes, one normaliser.

Measured on 2026-11-18, switching model inside a single session broke the whole
conversation from one message onwards:

* DeepSeek answered ``422 ... messages[25]: missing field 'type'`` because the
  history held an Ollama-shaped tool call;
* Ollama answered ``400 Value looks like object, but can't find closing '}'
  symbol`` because the history held an OpenAI-shaped one (``arguments`` as a
  string).

Nothing was wrong with the *storage*: each provider stores what it produced. The
bug was that the boundary replayed it unchanged. `request_messages(messages,
wire=...)` now rebuilds every call in the shape the next provider expects, and
repairs arguments a small local model truncated.
"""

from __future__ import annotations

import json

from uh.providers import request_messages

#: What Ollama returns and what ends up in the store.
OLLAMA_CALL = {
    "id": "call_o9d5u2xe",
    "function": {"index": 0, "name": "patch", "arguments": {"path": "a.py", "code": "x = 1"}},
}

#: What DeepSeek returns (already normalised by its own parser).
OPENAI_CALL = {
    "id": "call_abc123",
    "type": "function",
    "function": {"name": "patch", "arguments": '{"path": "a.py", "code": "x = 1"}'},
}


def history(call: dict) -> list[dict]:
    return [
        {"role": "user", "content": "corrige a.py"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call.get("id"), "content": '{"ok": true}'},
    ]


# --------------------------------------------------------------------------- #
# openai wire: what DeepSeek and every OpenAI-compatible endpoint demand
# --------------------------------------------------------------------------- #
def test_an_ollama_call_is_rebuilt_for_the_openai_wire() -> None:
    sent = request_messages(history(OLLAMA_CALL), wire="openai")

    call = sent[1]["tool_calls"][0]
    assert call["type"] == "function", "the field DeepSeek refused for lack of it"
    assert call["id"] == "call_o9d5u2xe", "the provider's own id is kept"
    assert "index" not in call and "index" not in call["function"], "streaming-only"
    assert isinstance(call["function"]["arguments"], str)
    assert json.loads(call["function"]["arguments"]) == {"path": "a.py", "code": "x = 1"}


def test_an_openai_call_is_left_alone() -> None:
    sent = request_messages(history(OPENAI_CALL), wire="openai")

    assert sent[1]["tool_calls"][0] == OPENAI_CALL


def test_a_truncated_argument_string_never_reaches_the_provider() -> None:
    """A small model cuts its own JSON; the turn must not die for that."""
    truncated = dict(OPENAI_CALL)
    truncated["function"] = {"name": "patch", "arguments": '{"path": "a.py", "code": "x = 1'}

    sent = request_messages(history(truncated), wire="openai")

    arguments = sent[1]["tool_calls"][0]["function"]["arguments"]
    assert isinstance(json.loads(arguments), dict), "valid JSON or {} - never a broken string"


def test_a_trailing_comma_is_repaired_rather_than_dropped() -> None:
    sloppy = dict(OPENAI_CALL)
    sloppy["function"] = {"name": "patch", "arguments": '{"path": "a.py",}'}

    sent = request_messages(history(sloppy), wire="openai")

    assert json.loads(sent[1]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}


def test_hopeless_arguments_become_an_empty_object() -> None:
    hopeless = dict(OPENAI_CALL)
    hopeless["function"] = {"name": "patch", "arguments": "pas du json du tout"}

    sent = request_messages(history(hopeless), wire="openai")

    assert json.loads(sent[1]["tool_calls"][0]["function"]["arguments"]) == {}


# --------------------------------------------------------------------------- #
# ollama wire: /api/chat wants arguments as an object
# --------------------------------------------------------------------------- #
def test_an_openai_call_is_rebuilt_for_the_ollama_wire() -> None:
    sent = request_messages(history(OPENAI_CALL), wire="ollama")

    call = sent[1]["tool_calls"][0]
    assert isinstance(call["function"]["arguments"], dict), "a string is what Ollama 400s on"
    assert call["function"]["arguments"] == {"path": "a.py", "code": "x = 1"}
    assert "type" not in call and "index" not in call["function"]


def test_an_ollama_call_passes_through_the_ollama_wire() -> None:
    sent = request_messages(history(OLLAMA_CALL), wire="ollama")

    call = sent[1]["tool_calls"][0]
    assert call["function"]["arguments"] == {"path": "a.py", "code": "x = 1"}
    assert "index" not in call["function"], "a replay is not a streaming delta"


# --------------------------------------------------------------------------- #
# the two rules that were already there must survive
# --------------------------------------------------------------------------- #
def test_an_empty_tool_calls_list_is_removed() -> None:
    sent = request_messages([{"role": "assistant", "content": "ok", "tool_calls": []}])

    assert "tool_calls" not in sent[0], "DeepSeek refuses the next turn over this"


def test_a_tool_message_without_id_inherits_the_call_it_answers() -> None:
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [OPENAI_CALL]},
        {"role": "tool", "content": '{"ok": true}'},
    ]

    sent = request_messages(messages, wire="openai")

    assert sent[1]["tool_call_id"] == "call_abc123"


def test_the_stored_messages_are_never_mutated() -> None:
    stored = history(OLLAMA_CALL)

    request_messages(stored, wire="openai")

    assert stored[1]["tool_calls"][0] == OLLAMA_CALL, "the session keeps what it stored"
