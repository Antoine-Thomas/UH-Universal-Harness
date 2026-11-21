"""Point 1: a turn never ends without an answer or a visible notice.

The rule has three exits from `run_turn` to cover, and it is stated once here so
the tests can be read as its contract:

1. a final answer — nothing to add but the answer itself;
2. the refusal limit (`_REFUSAL_LIMIT` identical refusals) — `run_turn` returns
   with the refusals in the transcript and no text;
3. ``BudgetExhausted`` — the loop ran out of iterations without a final answer.

Exits 2 and 3, and a plain empty answer, now leave a ``system`` message in the
store carrying the same sentence the browser received as a ``notice`` frame. The
invariant every test below holds: after `run_text`, the stored session contains
the user's message AND something readable after it — never a user message alone.
A turn the operator stopped (Ctrl+C, Stop) is the documented exception: it is
rolled back, because a turn cut in the middle is not a turn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import config as uh_config
from uh import console
from uh.providers import ProviderError
from uh.store import Store


class NoopToolset:
    name = "noop"
    tools = [
        {
            "type": "function",
            "function": {"name": "noop", "description": "", "parameters": {}},
        }
    ]

    def call(self, name: str, args: dict) -> dict:
        return {"ok": True}


def _call(name: str) -> dict:
    return {"function": {"name": name, "arguments": {}}}


def _session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider, *, max_iterations: int = 4
) -> console.ChatSession:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    store = Store(tmp_path / "uh.db")
    session = console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=store,
        max_iterations=max_iterations,
    )
    events: list[dict] = []
    session.events = events.append
    session._test_events = events  # type: ignore[attr-defined]
    session._test_store = store  # type: ignore[attr-defined]
    return session


def _stored(session: console.ChatSession) -> list[dict]:
    store: Store = session._test_store  # type: ignore[attr-defined]
    assert session.session_id is not None, "the turn must be stored"
    return store.get_messages(session.session_id)


def _notices(session: console.ChatSession) -> list[str]:
    return [
        str(event.get("message") or "")
        for event in session._test_events  # type: ignore[attr-defined]
        if event.get("type") == "notice"
    ]


class FinalAnswer:
    model = "stub:1b"

    def __init__(self, text: str = "voici la reponse") -> None:
        self.text = text
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        return {"role": "assistant", "content": self.text, "tool_calls": []}


class AlwaysCallsAnAllowedTool:
    """Never answers: it keeps calling a permitted tool until the budget runs out."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        return {"role": "assistant", "content": "", "tool_calls": [_call("noop")]}


class StuckOnAForbiddenTool:
    """The model of `test_a_model_stuck_on_a_forbidden_tool_stops_the_turn`."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        return {"role": "assistant", "content": "", "tool_calls": [_call("terminal")]}


class EmptyAnswer:
    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        return {"role": "assistant", "content": "", "tool_calls": []}


class BreaksDown:
    model = "stub:1b"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        raise ProviderError("HTTP 500 from the stub")


# --------------------------------------------------------------------------- #
# exit 1: a final answer
# --------------------------------------------------------------------------- #
def test_a_final_answer_stores_the_answer_and_no_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, monkeypatch, FinalAnswer())

    assert session.run_text("question") == 0

    stored = _stored(session)
    assert [row["role"] for row in stored] == ["user", "assistant"]
    assert stored[-1]["content"] == "voici la reponse"
    assert _notices(session) == [], "a normal turn must not be dressed up as a problem"


# --------------------------------------------------------------------------- #
# exit 2: the refusal limit (clean return, no text)
# --------------------------------------------------------------------------- #
def test_the_refusal_limit_turn_stores_a_readable_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = StuckOnAForbiddenTool()
    session = _session(tmp_path, monkeypatch, provider)
    session.toolsets = {"file": NoopToolset()}  # `terminal` is not offered: refused

    assert session.run_text("fais quelque chose") == 0

    assert provider.calls == 3, "the loop still stops at the refusal limit"
    stored = _stored(session)
    assert stored[0]["role"] == "user"
    assert stored[-1]["role"] == "system"
    assert stored[-1]["content"] == console.NO_ANSWER_NOTICE
    notices = _notices(session)
    # The loop's own notices come first (one per refusal, then the stop reason);
    # the closing sentence is the last thing the operator reads, and it is the
    # one the store keeps.
    assert any("tour arrêté" in notice for notice in notices)
    assert notices[-1] == console.NO_ANSWER_NOTICE


# --------------------------------------------------------------------------- #
# exit 3: BudgetExhausted
# --------------------------------------------------------------------------- #
def test_the_budget_exhausted_turn_stores_a_readable_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AlwaysCallsAnAllowedTool()
    session = _session(tmp_path, monkeypatch, provider, max_iterations=2)
    session.toolsets = {"noop": NoopToolset()}

    assert session.run_text("boucle") == 1, "a spent budget is a failed turn"

    assert provider.calls == 2
    stored = _stored(session)
    assert stored[0]["role"] == "user"
    assert stored[-1]["role"] == "system"
    assert "limite max_iterations" in stored[-1]["content"]
    assert _notices(session) == [console.BUDGET_NOTICE.format(calls=2)]


# --------------------------------------------------------------------------- #
# a plain empty answer, and a provider that dies
# --------------------------------------------------------------------------- #
def test_an_empty_answer_is_not_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, monkeypatch, EmptyAnswer())

    assert session.run_text("question") == 0

    stored = _stored(session)
    assert stored[0]["role"] == "user"
    assert stored[-1]["role"] == "system"
    assert stored[-1]["content"] == console.NO_ANSWER_NOTICE
    assert _notices(session) == [console.NO_ANSWER_NOTICE]


def test_a_dead_provider_stores_its_sentence_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The error bubble is live only, so the stored turn needs the sentence."""
    session = _session(tmp_path, monkeypatch, BreaksDown())

    assert session.run_text("question") == 1

    stored = _stored(session)
    assert [row["role"] for row in stored] == ["user", "system"]
    assert "ProviderError" in stored[-1]["content"]
    assert _notices(session) == [], "the error frame already said it live, once"


# --------------------------------------------------------------------------- #
# the invariant, over the three exits
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("factory", "exit_code"),
    [
        (lambda: FinalAnswer(), 0),
        (lambda: StuckOnAForbiddenTool(), 0),
        (lambda: AlwaysCallsAnAllowedTool(), 1),
        (lambda: EmptyAnswer(), 0),
    ],
    ids=["final-answer", "refusal-limit", "budget-exhausted", "empty-answer"],
)
def test_the_user_message_is_never_stored_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, factory, exit_code: int
) -> None:
    session = _session(tmp_path, monkeypatch, factory(), max_iterations=4)
    session.toolsets = {"noop": NoopToolset()}

    assert session.run_text("question") == exit_code

    stored = _stored(session)
    assert stored[0]["role"] == "user", "the operator's message is always kept"
    tail = [row for row in stored[1:] if str(row.get("content") or "").strip()]
    assert tail, "a stored turn never ends on the user's message alone"
