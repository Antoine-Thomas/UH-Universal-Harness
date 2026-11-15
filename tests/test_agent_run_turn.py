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


class RunnableToolset:
    """One tool that runs a snippet - the shape the code-block guard looks for."""

    name = "positron"

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "description": "run a snippet",
                    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
                },
            }
        ]

    def call(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        return {"output": "42"}


def test_a_code_block_is_refused_once_and_the_tool_is_called() -> None:
    """A model that answers with code is asked again, and the tool runs it.

    This is the loop's rule, not a per-provider one: nothing here knows which
    model answered, so a local model and a hosted one travel the same path.
    """
    toolset = RunnableToolset()
    provider = ScriptedProvider(
        [
            {"role": "assistant", "content": "Sure, run this:\n```python\nprint(41+1)\n```\n"},
            tool_call("execute_code", code="print(41+1)"),
            {"role": "assistant", "content": "42"},
        ]
    )
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "run it"}],
        provider,
        {"positron": toolset},
        on_notice=notices.append,
    )

    assert toolset.calls == [("execute_code", {"code": "print(41+1)"})]
    nudge = provider.calls[1][-1]
    assert nudge["role"] == "system", "the second call must carry the retry instruction"
    assert "execute_code" in nudge["content"], "a nudge that does not name the tool is advice"
    assert last_assistant_message(history)["content"] == "42"
    assert notices == [], "a corrected turn is not a notice"


def test_a_second_code_block_ends_the_turn_with_a_notice() -> None:
    """One retry, never a loop: the user is paying for every extra call."""
    toolset = RunnableToolset()
    provider = ScriptedProvider(
        [
            {"role": "assistant", "content": "```bash\nls\n```"},
            {"role": "assistant", "content": "```bash\nls -la\n```"},
        ]
    )
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "list the files"}],
        provider,
        {"positron": toolset},
        on_notice=notices.append,
    )

    assert toolset.calls == []
    assert len(provider.calls) == 2, "exactly one retry"
    assert len(notices) == 1 and "execute_code" in notices[0]
    assert last_assistant_message(history)["content"].startswith("```bash"), "the answer still reaches the user"


def test_a_code_explanation_with_no_runnable_tool_is_left_alone(tmp_path: Path) -> None:
    """The guard must not tax a question that was never about running anything."""
    provider = ScriptedProvider([{"role": "assistant", "content": "```python\nprint(1)\n```"}])

    run_turn(
        [{"role": "user", "content": "how would that look in python?"}],
        provider,
        {"file": FileToolset(workspace=tmp_path)},
    )

    assert len(provider.calls) == 1
