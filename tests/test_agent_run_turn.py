"""run_turn: tool execution, message contract, budget and fail-closed behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uh.agent import BudgetExhausted, last_assistant_message, run_turn
from uh.toolsets.file import FileToolset


class ScriptedProvider:
    """Replays canned assistant messages, no network."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append([dict(message) for message in messages])  # snapshot, not the live list
        if not self.replies:
            raise AssertionError("provider called more times than scripted")
        return self.replies.pop(0)


def tool_call(name: str, **arguments) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def test_tool_call_then_final_answer(tmp_path: Path) -> None:
    toolset = FileToolset(workspace=tmp_path)
    provider = ScriptedProvider(
        [
            tool_call("write_file", path="out.txt", content="ok"),
            {"role": "assistant", "content": "done"},
        ]
    )

    history = run_turn([{"role": "user", "content": "write it"}], provider, {"file": toolset})

    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"
    assert [message["role"] for message in history] == ["user", "assistant", "tool", "assistant"]
    assert history[2]["name"] == "write_file"
    assert json.loads(history[2]["content"])["path"] == "out.txt"
    assert last_assistant_message(history)["content"] == "done"
    # the tool schema reached the provider, otherwise the model could not call it
    assert provider.calls[0] == [{"role": "user", "content": "write it"}]


def test_tool_definitions_are_sent_to_provider(tmp_path: Path) -> None:
    seen: dict = {}

    class Recorder:
        def chat(self, messages: list[dict], tools: list[dict]) -> dict:
            seen["tools"] = tools
            return {"role": "assistant", "content": "ok"}

    run_turn([{"role": "user", "content": "hi"}], Recorder(), {"file": FileToolset(workspace=tmp_path)})

    names = [tool["function"]["name"] for tool in seen["tools"]]
    assert {"read_file", "write_file", "patch", "search_files"} <= set(names)


def test_budget_exhausted_when_provider_always_calls_tools(tmp_path: Path) -> None:
    class LoopingProvider:
        def chat(self, messages: list[dict], tools: list[dict]) -> dict:
            return tool_call("read_file", path="a.txt")

    with pytest.raises(BudgetExhausted) as excinfo:
        run_turn(
            [{"role": "user", "content": "loop"}],
            LoopingProvider(),
            {"file": FileToolset(workspace=tmp_path)},
            max_iterations=1,
        )

    assert "max_iterations=1" in str(excinfo.value)


def test_tool_exception_becomes_tool_message(tmp_path: Path) -> None:
    class ExplodingToolset:
        name = "boom"
        tools = [
            {
                "type": "function",
                "function": {"name": "boom", "description": "", "parameters": {"type": "object"}},
            }
        ]

        def call(self, name: str, args: dict) -> dict:
            raise RuntimeError("kaboom")

    provider = ScriptedProvider([tool_call("boom"), {"role": "assistant", "content": "recovered"}])

    history = run_turn([{"role": "user", "content": "x"}], provider, {"boom": ExplodingToolset()})

    assert history[-1]["content"] == "recovered"
    tool_message = [message for message in history if message["role"] == "tool"][0]
    assert "kaboom" in tool_message["content"]
    assert "error" in json.loads(tool_message["content"])


def test_unknown_tool_does_not_crash(tmp_path: Path) -> None:
    provider = ScriptedProvider([tool_call("nope"), {"role": "assistant", "content": "ok"}])

    history = run_turn(
        [{"role": "user", "content": "x"}], provider, {"file": FileToolset(workspace=tmp_path)}
    )

    payload = json.loads(history[2]["content"])
    assert "unknown tool" in payload["error"]


def test_sandbox_error_is_reported_not_raised(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    provider = ScriptedProvider(
        [
            tool_call("write_file", path="../escape.txt", content="x"),
            {"role": "assistant", "content": "refused"},
        ]
    )

    history = run_turn(
        [{"role": "user", "content": "escape"}], provider, {"file": FileToolset(workspace=workspace)}
    )

    payload = json.loads(history[2]["content"])
    assert "outside the workspace" in payload["error"]
    assert not (tmp_path / "escape.txt").exists()


def test_original_message_list_is_not_mutated(tmp_path: Path) -> None:
    original = [{"role": "user", "content": "hi"}]
    provider = ScriptedProvider([{"role": "assistant", "content": "hello"}])

    history = run_turn(original, provider, {"file": FileToolset(workspace=tmp_path)})

    assert original == [{"role": "user", "content": "hi"}]
    assert history is not original
    assert len(history) == 2
