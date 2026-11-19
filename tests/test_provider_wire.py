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


# --------------------------------------------------------------------------- #
# the sequence must stay legal: this is what produced 422/400 after a crash
# --------------------------------------------------------------------------- #
def test_an_orphan_call_gets_an_answer_before_the_request_leaves() -> None:
    """An interrupted turn stored a call and no result: the provider refuses that."""
    broken = [
        {"role": "user", "content": "dessine"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "execute_code", "arguments": '{"code": "x"}'}},
                {"id": "c2", "function": {"name": "execute_code", "arguments": '{"code": "y"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "execute_code", "content": '{"ok": true}'},
    ]

    sent = request_messages(broken, wire="openai")

    answered = [m.get("tool_call_id") for m in sent if m.get("role") == "tool"]
    assert answered == ["c1", "c2"], "every call is answered, in order"
    synthetic = json.loads(sent[-1]["content"])
    assert synthetic["reason"] == "orphan_call"
    assert synthetic["tool"] == "execute_code"


def test_an_answer_nobody_asked_for_is_dropped() -> None:
    stray = [
        {"role": "user", "content": "x"},
        {"role": "tool", "tool_call_id": "ghost", "name": "execute_code", "content": "{}"},
    ]

    sent = request_messages(stray, wire="openai")

    assert [m["role"] for m in sent] == ["user"], "a strict API rejects an unasked answer"


def test_a_clean_history_is_untouched() -> None:
    clean = history(OPENAI_CALL)
    assert request_messages(clean, wire="openai") == clean


def test_every_assistant_call_is_answered_before_the_next_turn() -> None:
    """The property, not a case: whatever the store holds, the request is legal."""
    messy = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "", "tool_calls": [dict(OLLAMA_CALL)]},
        {"role": "user", "content": "b"},  # the turn died here, no tool answer
        {"role": "assistant", "content": "", "tool_calls": [dict(OPENAI_CALL)]},
        {"role": "tool", "tool_call_id": "call_abc123", "name": "patch", "content": "{}"},
    ]

    sent = request_messages(messy, wire="openai")

    open_ids: list[str] = []
    for message in sent:
        if message.get("role") == "assistant":
            assert not open_ids, f"unanswered call(s) {open_ids} before a new assistant message"
            open_ids = [call["id"] for call in message.get("tool_calls") or []]
        elif message.get("role") == "tool":
            if message.get("tool_call_id") in open_ids:
                open_ids.remove(message["tool_call_id"])
        elif message.get("role") == "user":
            assert not open_ids, f"unanswered call(s) {open_ids} before a user message"
    assert not open_ids


# --------------------------------------------------------------------------- #
# what SQLite hands back, and what a strict gateway accepts
# --------------------------------------------------------------------------- #
def test_a_row_read_from_sqlite_gets_type_function_on_the_way_out() -> None:
    """The exact regression of 1.8.0: a stored call replayed without `type`.

    The row is what the session actually holds - a JSON column, Ollama's shape,
    arguments as an object - and the outgoing message must carry `type: function`
    and a JSON *string*. DeepSeek/OpenAI answered
    ``422 messages[N]: missing field 'type'`` otherwise.
    """
    stored = json.dumps(
        [{"id": "call_ray9", "function": {"index": 0, "name": "execute_code", "arguments": {"code": "print(1)"}}}]
    )
    history_from_db = [
        {"role": "user", "content": "dessine"},
        {"role": "assistant", "content": "", "tool_calls": json.loads(stored)},
        {"role": "tool", "tool_call_id": "call_ray9", "name": "execute_code", "content": "1"},
    ]

    sent = request_messages(history_from_db, wire="openai")
    call = sent[1]["tool_calls"][0]

    assert call["type"] == "function"
    assert call["id"] == "call_ray9"
    assert isinstance(call["function"]["arguments"], str)
    assert "index" not in call["function"]


def test_a_call_to_a_tool_the_request_does_not_declare_is_dropped() -> None:
    """OmniRouter refused the whole request: `mcp__positron__execute_code` was not
    in request.tools. A tool that is no longer offered cannot be replayed."""
    history = [
        {"role": "user", "content": "dessine"},
        {
            "role": "assistant",
            "content": "je dessine",
            "tool_calls": [
                {
                    "id": "call_ghost",
                    "type": "function",
                    "function": {"name": "mcp__positron__execute_code", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_ghost", "name": "mcp__positron__execute_code", "content": "{}"},
    ]
    tools = [{"type": "function", "function": {"name": "execute_code", "parameters": {}}}]

    sent = request_messages(history, wire="openai", tools=tools)

    names = [
        call["function"]["name"]
        for message in sent
        if message.get("tool_calls")
        for call in message["tool_calls"]
    ]
    assert "mcp__positron__execute_code" not in names
    assert not [m for m in sent if m.get("role") == "tool"], "its answer goes too"
    assert [m["role"] for m in sent] == ["user", "assistant"], "the text survives"
    assert sent[1]["content"] == "je dessine"


def test_a_declared_call_is_kept_when_the_tool_list_is_given() -> None:
    tools = [{"type": "function", "function": {"name": "patch", "parameters": {}}}]
    sent = request_messages(history(OPENAI_CALL), wire="openai", tools=tools)
    assert sent[1]["tool_calls"][0]["function"]["name"] == "patch"


def test_without_a_tool_list_nothing_is_filtered() -> None:
    """Opt-in on purpose: existing callers keep their behaviour."""
    history_db = history(OLLAMA_CALL)
    assert request_messages(history_db, wire="openai") == request_messages(
        history_db, wire="openai", tools=None
    )
