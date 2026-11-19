"""A refused call must say WHICH refusal, and a stuck model must be stopped.

Measured on 2026-11-18: the interface reported « `patch` n'est pas autorisé dans
ce mode » while `patch` was offered, and the stored call carried
``{"code": …, "path": …}`` - an argument the tool does not have. One sentence
covered two different failures, and the operator spent an hour looking at modes
and personas instead of at the call.

Three rules now, each pinned below:

* an unknown tool and an invalid call are different refusals, and the invalid one
  names the arguments the tool expects;
* nothing is executed when the schema rejects the call - including the ``{}`` a
  repaired argument string can collapse to;
* the same invalid call three times in a row stops the turn and says why.
"""

from __future__ import annotations

import json

from uh.agent import _argument_problem, _execute, run_turn
from uh.toolsets.file import FileToolset


class RecordingToolset:
    """The smallest toolset that can prove "nothing was executed"."""

    name = "probe"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "probe",
                "description": "records a call",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "flag": {"type": "boolean"}},
                    "required": ["path"],
                },
            },
        }
    ]

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def call(self, name: str, args: dict | None = None) -> dict:
        self.calls.append(dict(args or {}))
        return {"ok": True, "args": args or {}}


class ScriptedProvider:
    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append([dict(message) for message in messages])
        if not self.replies:
            raise AssertionError("provider called more times than scripted")
        return self.replies.pop(0)


def assistant(*calls: dict) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def call(name: str, call_id: str, **arguments) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def final(text: str = "done") -> dict:
    return {"role": "assistant", "content": text}


def tool_messages(history: list[dict]) -> list[dict]:
    return [message for message in history if message.get("role") == "tool"]


# --------------------------------------------------------------------------- #
# the two refusals are told apart
# --------------------------------------------------------------------------- #
def test_an_unknown_tool_is_forbidden_and_says_so() -> None:
    toolset = RecordingToolset()

    message, refused = _execute({"probe": toolset}, "nope", {})

    payload = json.loads(message["content"])
    assert refused is True
    assert payload["type"] == "forbidden"
    assert payload["reason"] == "unknown_tool"
    assert "Available" in payload["error"]
    assert toolset.calls == []


def test_execute_code_absent_names_the_switch_instead_of_the_mode() -> None:
    """The absence has an actionable cause; the old sentence hid it."""
    message, refused = _execute({}, "execute_code", {})

    payload = json.loads(message["content"])
    assert refused is True
    assert payload["reason"] == "execute_code_not_exposed"
    assert "UH_UHB_EXECUTE" in payload["error"], "the operator must be told what to set"
    assert "not available in this mode" not in payload["error"]


# --------------------------------------------------------------------------- #
# arguments are validated before anything runs
# --------------------------------------------------------------------------- #
def test_an_unknown_argument_is_refused_with_the_expected_list() -> None:
    toolset = RecordingToolset()

    message, refused = _execute({"probe": toolset}, "probe", {"path": "a", "code": "x"})

    payload = json.loads(message["content"])
    assert refused is True
    assert payload["type"] == "invalid_arguments"
    assert "code" in payload["error"] and "path" in payload["error"]
    assert payload["expected"] == ["flag", "path"]
    assert toolset.calls == [], "an invalid call executes nothing"


def test_a_missing_required_argument_is_refused() -> None:
    toolset = RecordingToolset()

    message, refused = _execute({"probe": toolset}, "probe", {})

    payload = json.loads(message["content"])
    assert refused is True
    assert payload["type"] == "invalid_arguments"
    assert "path" in payload["error"]
    assert toolset.calls == [], "the {} a repair can produce must never run a required-arg tool"


def test_a_valid_call_still_runs() -> None:
    toolset = RecordingToolset()

    message, refused = _execute({"probe": toolset}, "probe", {"path": "a"})

    assert refused is False
    assert json.loads(message["content"])["ok"] is True
    assert toolset.calls == [{"path": "a"}]


def test_a_patch_call_with_a_code_argument_is_an_argument_error() -> None:
    """The exact stored call that produced « not allowed in this mode »."""
    index = {"patch": FileToolset(workspace=".")}

    message, refused = _execute(index, "patch", {"code": "x = 1", "path": "a.py"})

    payload = json.loads(message["content"])
    assert refused is True
    assert payload["type"] == "invalid_arguments"
    assert "old_string" in payload["error"]
    assert payload["reason"] != "unknown_tool", "the tool exists; the call was wrong"


def test_the_schema_check_is_generic() -> None:
    """One reader for every toolset: the schema the model was shown."""
    toolset = RecordingToolset()
    assert _argument_problem(toolset, "probe", {"path": "a"}) is None
    assert _argument_problem(toolset, "probe", {"nope": 1}) is not None


# --------------------------------------------------------------------------- #
# the loop guard
# --------------------------------------------------------------------------- #
def test_the_same_invalid_call_thrice_stops_the_turn() -> None:
    toolset = RecordingToolset()
    same = {"path": "a", "code": "x"}
    provider = ScriptedProvider([assistant(call("probe", f"c{i}", **same)) for i in range(1, 4)])
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "vas-y"}],
        provider,
        {"probe": toolset},
        max_iterations=8,
        on_notice=notices.append,
    )

    assert len(provider.calls) == 3, "the turn stops on the third refusal, it does not pay for more"
    assert len(tool_messages(history)) == 3
    assert toolset.calls == [], "no iteration ever executed the call"
    assert any("tour arrêté" in notice for notice in notices)
    assert any("invalid_arguments" in notice for notice in notices)


def test_the_same_tool_with_other_arguments_is_not_the_same_call() -> None:
    toolset = RecordingToolset()
    provider = ScriptedProvider(
        [
            assistant(call("probe", "c1", nope=1)),
            assistant(call("probe", "c2", path="a")),
            final(),
        ]
    )

    history = run_turn([{"role": "user", "content": "vas-y"}], provider, {"probe": toolset})

    assert toolset.calls == [{"path": "a"}], "the valid attempt runs on the next iteration"
    assert history[-1]["content"] == "done"
