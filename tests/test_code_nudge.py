"""Point 3: a code block in place of a tool call is re-asked, twice at most.

The rule has two halves and both are tested here:

* **it fires when a tool could have done the work.** A model that answers
  ```python …``` while `execute_code` is offered is asked, in the turn, to call it
  — twice at most (`_NUDGE_LIMIT`), and the turn ends with a visible notice if it
  still refuses. The snippet stays in the transcript, unexecuted, and says so.
* **it never fires where the tool is absent.** In `chat` (read-only) a code block
  IS the deliverable; re-asking would burn a provider call to demand a tool the
  mode withheld. The same holds when the only offered tools only read.

The second half is the reason the first one is safe: the mode gate of point 2 runs
before `run_turn`, so "a runnable tool is offered" is the single condition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import config as uh_config
from uh import console
from uh.agent import _NUDGE, _NUDGE_LIMIT, last_assistant_message, run_turn
from uh.toolsets.file import FileToolset


class ScriptedProvider:
    """Replays canned assistant messages; records every call it was given."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        self.calls.append([dict(message) for message in messages])
        if not self.replies:
            raise AssertionError("provider called more times than scripted")
        return self.replies.pop(0)


def code(content: str = "print(41+1)", fence: str = "python") -> str:
    return f"Voici :\n```{fence}\n{content}\n```\n"


def tool_call(name: str, **arguments) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": arguments}}],
    }


class RunnableToolset:
    """One tool that runs something, and nothing else — what `dev` offers."""

    name = "positron"
    tools = [
        {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "run a snippet",
                "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
            },
        }
    ]

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, args: dict) -> dict:
        self.calls.append((name, args))
        return {"ok": True, "stdout": "42\n"}


# --------------------------------------------------------------------------- #
# the retry, and its bound
# --------------------------------------------------------------------------- #
def test_the_first_code_block_is_reasked_with_the_tool_named() -> None:
    toolset = RunnableToolset()
    provider = ScriptedProvider([{"role": "assistant", "content": code()}, tool_call("execute_code", code="print(41+1)"), {"role": "assistant", "content": "42"}])
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "calcule 41+1"}], provider, {"positron": toolset},
        on_notice=notices.append, max_iterations=6,
    )

    assert toolset.calls == [("execute_code", {"code": "print(41+1)"})], "the snippet ran"
    nudge = provider.calls[1][-1]
    assert nudge["role"] == "system"
    assert "`execute_code`" in nudge["content"], nudge["content"]
    assert "Do not paste the code" in nudge["content"], "the instruction must close the loophole"
    assert last_assistant_message(history)["content"] == "42"
    assert notices == [], "a corrected turn is not a notice"


def test_two_relances_are_allowed_and_the_second_one_still_runs_the_tool() -> None:
    """The budget of point 3: a small local model often needs the format twice."""
    toolset = RunnableToolset()
    provider = ScriptedProvider(
        [
            {"role": "assistant", "content": code("print(1)")},
            {"role": "assistant", "content": code("print(2)", fence="bash")},
            tool_call("execute_code", code="print(3)"),
            {"role": "assistant", "content": "voilà"},
        ]
    )

    history = run_turn(
        [{"role": "user", "content": "fais-le"}], provider, {"positron": toolset}, max_iterations=6
    )

    assert len(provider.calls) == 4
    assert toolset.calls == [("execute_code", {"code": "print(3)"})]
    nudges = [call[-1] for call in provider.calls[1:] if call[-1]["role"] == "system"]
    assert len(nudges) == 2
    assert last_assistant_message(history)["content"] == "voilà"


def test_the_third_code_block_ends_the_turn_and_says_so() -> None:
    toolset = RunnableToolset()
    provider = ScriptedProvider(
        [{"role": "assistant", "content": code("print(1)")} for _ in range(_NUDGE_LIMIT + 1)]
    )
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "fais-le"}], provider, {"positron": toolset},
        on_notice=notices.append, max_iterations=8,
    )

    assert toolset.calls == [], "nothing was executed: the model never called the tool"
    assert len(provider.calls) == _NUDGE_LIMIT + 1, "one answer, two relances, then stop"
    assert len(notices) == 1
    assert f"{_NUDGE_LIMIT} relances" in notices[0]
    assert "pas été exécuté" in notices[0]
    assert last_assistant_message(history)["content"].startswith("Voici"), (
        "the snippet the model insisted on is still shown, not swallowed"
    )


def test_the_nudge_names_the_one_tool_that_can_run() -> None:
    assert "{tool}" in _NUDGE
    toolset = RunnableToolset()
    provider = ScriptedProvider([{"role": "assistant", "content": code()}, {"role": "assistant", "content": "ok"}])

    run_turn([{"role": "user", "content": "x"}], provider, {"positron": toolset}, max_iterations=4)

    assert "`execute_code`" in provider.calls[1][-1]["content"]


# --------------------------------------------------------------------------- #
# where it must NOT fire
# --------------------------------------------------------------------------- #
def test_a_plain_answer_is_never_reasked() -> None:
    provider = ScriptedProvider([{"role": "assistant", "content": "42"}])

    run_turn([{"role": "user", "content": "2+40 ?"}], provider, {"positron": RunnableToolset()})

    assert len(provider.calls) == 1


def test_a_turn_with_no_toolset_is_never_reasked(tmp_path: Path) -> None:
    """`chat` before any catalogue: a code block is the deliverable, not a miss."""
    provider = ScriptedProvider([{"role": "assistant", "content": code()}])
    notices: list[str] = []

    history = run_turn(
        [{"role": "user", "content": "montre-moi le code"}], provider, {}, on_notice=notices.append
    )

    assert len(provider.calls) == 1
    assert notices == []
    assert last_assistant_message(history)["content"].startswith("Voici")


def test_read_only_tools_do_not_trigger_a_retry(tmp_path: Path) -> None:
    """The mode gate leaves `read_file` only; nothing can run, so nothing is demanded."""
    provider = ScriptedProvider([{"role": "assistant", "content": code()}])
    notices: list[str] = []

    run_turn(
        [{"role": "user", "content": "montre-moi le code"}],
        provider,
        {"file": FileToolset(workspace=tmp_path)},
        on_notice=notices.append,
    )

    assert len(provider.calls) == 1
    assert notices == []


def test_a_request_that_asks_for_the_code_is_never_reasked() -> None:
    """« montre-moi le code » is not a missed call: the nudge would fight the user."""
    provider = ScriptedProvider([{"role": "assistant", "content": code()}])
    notices: list[str] = []

    run_turn(
        [{"role": "user", "content": "montre-moi le code qui trace la courbe"}],
        provider,
        {"positron": RunnableToolset()},
        on_notice=notices.append,
    )

    assert len(provider.calls) == 1, "no relance when the code IS the deliverable"
    assert notices == []


def test_only_the_last_user_message_decides_the_guard() -> None:
    """An older turn asking for a snippet must not silence today's nudge."""
    toolset = RunnableToolset()
    provider = ScriptedProvider([{"role": "assistant", "content": code()}, tool_call("execute_code", code="x=1"), {"role": "assistant", "content": "fait"}])

    run_turn(
        [
            {"role": "user", "content": "montre-moi le code d'un tri"},
            {"role": "assistant", "content": "voilà"},
            {"role": "user", "content": "et maintenant trace la courbe de x^2"},
        ],
        provider,
        {"positron": toolset},
        max_iterations=5,
    )

    assert toolset.calls, "the tool was called after the relance"
    assert provider.calls[1][-1]["role"] == "system"


def test_the_retry_instruction_is_not_stored_and_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nudge is protocol: it reaches the provider, never the operator's thread."""
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    from uh.store import Store

    store = Store(tmp_path / "uh.db")
    provider = ScriptedProvider(
        [
            {"role": "assistant", "content": code("print(7)")},
            tool_call("execute_code", code="print(7)"),
            {"role": "assistant", "content": "voilà"},
        ]
    )
    session = console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets={"positron": RunnableToolset()},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=store,
        max_iterations=6,
        mode="dev",
    )

    assert session.run_text("calcule 7") == 0

    assert session.last_nudges == 1, "the model was re-asked once"
    stored = store.get_messages(session.session_id)
    assert not [m for m in stored if m["role"] == "system"], "no protocol text in the thread"
    assert [m["role"] for m in stored][0] == "user"
    assert any(m["role"] == "tool" for m in stored), "the code really ran"
    assert provider.calls[1][-1]["role"] == "system", "the provider did see the instruction"
    store.close()


def test_chat_mode_with_execute_code_in_the_catalogue_is_never_reasked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The integration case of point 2 + 3: the gate withholds it, so no nudge.

    Before point 2 this turn would have offered `execute_code` and re-asked twice
    in a read-only mode. Now the tool is gone from the offer, `run_turn` sees no
    runnable tool, and the code answer goes through unchanged - one paid call.
    """
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    provider = ScriptedProvider([{"role": "assistant", "content": code()}])
    store = None
    session = console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets={"positron": RunnableToolset()},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=store,
        max_iterations=4,
        mode="chat",
    )

    assert session.run_text("montre-moi le code") == 0

    assert provider.calls and len(provider.calls) == 1, "one call, no relance"
    assert session.last_withheld == ["execute_code"], "the gate is what removed it"
    assert all(call[-1]["role"] != "system" for call in provider.calls), "no nudge was injected"
