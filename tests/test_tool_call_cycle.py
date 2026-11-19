"""P0-6 — the tool-call cycle: ids, refusals, loops, persistence.

UHB owns the permission policy. This file pins the *transport* side in UH: what
an answer must carry to be matched to its question, what a refusal looks like,
and what happens when a model keeps asking for a tool the current mode does not
offer.

Measured before this lot, on the code that shipped 0.1.x:

- tool messages carried ``role``, ``name``, ``content`` and **no
  ``tool_call_id``**: two parallel calls were indistinguishable, and a session
  replayed from SQLite sent unlabelled tool answers to strict providers;
- an unavailable tool produced ``{"error": "unknown tool ..."}`` — a sentence, not
  a typed refusal, and nothing stopped a model from asking twice;
- the tool message was not stored with its id at all, because the column did not
  exist.
"""

from __future__ import annotations

import json
from pathlib import Path

from uh.agent import run_turn
from uh.store import Store
from uh.toolsets.file import FileToolset


class ScriptedProvider:
    """Replays canned assistant messages and records what it was sent."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append([dict(message) for message in messages])
        if not self.replies:
            raise AssertionError("provider called more times than scripted")
        return self.replies.pop(0)


def call(name: str, call_id: str | None = None, **arguments) -> dict:
    payload: dict = {"function": {"name": name, "arguments": arguments}}
    if call_id:
        payload["id"] = call_id
    return payload


def assistant(*calls: dict) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def final(text: str = "done") -> dict:
    return {"role": "assistant", "content": text}


def tool_messages(history: list[dict]) -> list[dict]:
    return [message for message in history if message.get("role") == "tool"]


def test_provider_ids_are_kept_and_each_answer_names_its_call(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            assistant(call("read_file", "call_a", path="a.txt"), call("read_file", "call_b", path="b.txt")),
            final(),
        ]
    )
    (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")
    (tmp_path / "b.txt").write_text("BBB", encoding="utf-8")

    history = run_turn(
        [{"role": "user", "content": "x"}], provider, {"file": FileToolset(workspace=tmp_path)}
    )

    tools = tool_messages(history)
    assert [message["tool_call_id"] for message in tools] == ["call_a", "call_b"]
    # Parallel calls, two answers, and each answer holds its own file's text: with
    # no id, the second result lands where the first was expected.
    assert "AAA" in tools[0]["content"]
    assert "BBB" in tools[1]["content"]


def test_a_provider_without_ids_gets_them_assigned_and_mirrored(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [assistant(call("read_file", path="a.txt"), call("read_file", path="a.txt")), final()]
    )
    (tmp_path / "a.txt").write_text("AAA", encoding="utf-8")

    history = run_turn(
        [{"role": "user", "content": "x"}], provider, {"file": FileToolset(workspace=tmp_path)}
    )

    assigned = [entry["id"] for entry in history[1]["tool_calls"]]
    assert all(assigned), "an id is assigned before the call is executed"
    assert len(set(assigned)) == 2, "two calls must not share an id"
    assert [message["tool_call_id"] for message in tool_messages(history)] == assigned


def test_a_forbidden_tool_is_typed_and_visible_in_the_transcript(tmp_path: Path) -> None:
    notices: list[str] = []
    provider = ScriptedProvider([assistant(call("terminal", "call_t", command="rm -rf /")), final()])

    history = run_turn(
        [{"role": "user", "content": "x"}],
        provider,
        {"file": FileToolset(workspace=tmp_path)},
        on_notice=notices.append,
    )

    payload = json.loads(tool_messages(history)[0]["content"])
    assert payload["type"] == "forbidden"
    assert payload["tool"] == "terminal"
    assert tool_messages(history)[0]["tool_call_id"] == "call_t"
    assert notices and "terminal" in notices[0], "the refusal must be shown, not swallowed"


def test_a_forbidden_call_does_not_cancel_an_allowed_one(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            assistant(call("terminal", "call_t"), call("write_file", "call_w", path="ok.txt", content="ok")),
            final(),
        ]
    )

    history = run_turn(
        [{"role": "user", "content": "x"}], provider, {"file": FileToolset(workspace=tmp_path)}
    )

    tools = tool_messages(history)
    assert json.loads(tools[0]["content"])["type"] == "forbidden"
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "ok", (
        "one refused call must not stop the others in the same message"
    )


def test_a_model_stuck_on_a_forbidden_tool_stops_the_turn(tmp_path: Path) -> None:
    """Three identical refusals, then the turn ends: the budget is not burned on a dead end.

    The limit is per (tool, arguments) and was raised from 2 to 3 on 2026-11-18:
    a model that invents one argument often fixes it on the second try, so the
    stop has to be late enough to let a corrected call through.
    """
    provider = ScriptedProvider(
        [
            assistant(call("terminal")),
            assistant(call("terminal")),
            assistant(call("terminal")),
            final("should never be reached"),
        ]
    )
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "x"}],
        provider,
        {"file": FileToolset(workspace=tmp_path)},
        on_notice=notices.append,
    )

    assert len(provider.calls) == 3, f"the loop must stop at the limit, got {len(provider.calls)} calls"
    assert len(tool_messages(history)) == 3
    assert any("tour arrêté" in notice for notice in notices)
    assert history[-1]["role"] == "tool", "the turn ends on the refusal, which stays in the transcript"


def test_the_answer_keeps_its_id_when_the_session_is_replayed(tmp_path: Path) -> None:
    """A stored tool message without its id breaks a resumed session on strict backends."""
    store = Store(tmp_path / "uh.db")
    try:
        session_id = store.create_session(model="stub:1b", provider="stub")
        store.append_message(session_id, {"role": "user", "content": "trace"})
        store.append_message(
            session_id,
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_x", "function": {"name": "read_file", "arguments": {}}}],
            },
        )
        store.append_message(
            session_id,
            {"role": "tool", "name": "read_file", "content": "{}", "tool_call_id": "call_x"},
        )

        stored = store.get_messages(session_id)
    finally:
        store.close()

    tool_row = [row for row in stored if row["role"] == "tool"][0]
    assert tool_row["tool_call_id"] == "call_x"
    assert tool_row["name"] == "read_file"
    assert stored[1]["tool_calls"][0]["id"] == "call_x", "the pair survives together"


def test_describe_tools_then_a_forbidden_call_answers_on_the_right_ids(tmp_path: Path) -> None:
    """The integration that matters: consult the catalogue, then hit the wall.

    1. the model asks `describe_tools` - UHB answers, verbatim, with the reason;
    2. it calls a tool the mode does not offer - the loop refuses, with the id of
       that call, and the turn is still usable (no crash, no loop);
    3. both answers survive a round-trip through SQLite with their ids, so a
       session resumed later can still tell which answer belongs to which call.
    """
    import httpx

    from uh.toolsets.uhb import Uhbtoolset

    catalogue = {
        "tools": [
            {
                "name": "execute_code",
                "description": "Execute Python code.",
                "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
                "category": "code",
                "compatible_modes": ["dev"],
                "available": False,
                "reason": {"code": "not_in_mode", "mode": "chat", "available_in": ["dev"],
                           "hint": "the current mode does not declare this tool"},
            }
        ],
        "count": 1,
        "mode": "chat",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "describe_tools must never execute anything"
        return httpx.Response(200, json=catalogue)

    uhb_toolset = Uhbtoolset("http://127.0.0.1:8101", client=httpx.Client(
        transport=httpx.MockTransport(handler)))

    provider = ScriptedProvider(
        [
            assistant(call("describe_tools", "call_desc")),
            assistant(call("execute_code", "call_exec", code="print(1)")),
            final("je ne peux pas exécuter ce code dans ce mode"),
        ]
    )

    history = run_turn(
        [{"role": "user", "content": "peux-tu tracer un graphe ?"}],
        provider,
        {"file": FileToolset(workspace=tmp_path), "uhb": uhb_toolset},
        on_notice=lambda _text: None,
    )

    tools = tool_messages(history)
    assert [message["tool_call_id"] for message in tools] == ["call_desc", "call_exec"]

    consulted = json.loads(tools[0]["content"])
    assert consulted["tools"][0]["available"] is False
    assert consulted["tools"][0]["reason"]["code"] == "not_in_mode"

    refused = json.loads(tools[1]["content"])
    assert refused["type"] == "forbidden"
    assert refused["tool"] == "execute_code"
    assert history[-1]["content"].startswith("je ne peux pas")

    store = Store(tmp_path / "uh.db")
    try:
        session_id = store.create_session(model="stub:1b", provider="stub")
        store.append_messages(session_id, history[1:])
        stored = store.get_messages(session_id)
    finally:
        store.close()

    stored_ids = [row.get("tool_call_id") for row in stored if row["role"] == "tool"]
    assert stored_ids == ["call_desc", "call_exec"], "the correlation survives a reload"


def test_one_allowed_execution_and_one_forbidden_tool_in_the_same_message(
    tmp_path: Path, monkeypatch
) -> None:
    """P0-4's acceptance: a message with two calls, one allowed and one not.

    The allowed one runs (UHB answered 200), the forbidden one gets a refusal tied
    to its own id, the turn keeps a usable answer, and the loop does not spin.
    """
    import httpx

    from uh.toolsets.uhb import Uhbtoolset

    monkeypatch.setenv("UH_UHB_EXECUTE", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"tools": [
                {"name": "execute_code", "description": "Run Python.",
                 "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
                 "execution_type": "subprocess"},
            ]})
        if request.url.path == "/tools/approvals":
            return httpx.Response(200, json={"approval_id": "nonce-cycle",
                                             "tool": "execute_code", "expires_in": 120})
        return httpx.Response(200, json={"ok": True, "stdout": "4\n", "exit_code": 0,
                                         "duration_s": 0.1, "images": []})

    uhb_toolset = Uhbtoolset("http://127.0.0.1:8101",
                             client=httpx.Client(transport=httpx.MockTransport(handler)),
                             discover=True,
                             approver=lambda _tool, _arguments: True)

    provider = ScriptedProvider(
        [
            assistant(call("execute_code", "call_run", code="print(2+2)"),
                      call("desktop_click", "call_click", ref="x:0")),
            final("voici 4"),
        ]
    )

    history = run_turn(
        [{"role": "user", "content": "calcule 2+2 puis clique"}],
        provider,
        {"file": FileToolset(workspace=tmp_path), "uhb": uhb_toolset},
        on_notice=lambda _text: None,
    )

    tools = tool_messages(history)
    assert [message["tool_call_id"] for message in tools] == ["call_run", "call_click"]
    assert json.loads(tools[0]["content"])["stdout"] == "4\n"
    refusal = json.loads(tools[1]["content"])
    assert refusal["type"] == "forbidden" and refusal["tool"] == "desktop_click"
    assert history[-1]["content"] == "voici 4"
