"""The Decision view: what a turn recorded, and what it refuses to invent.

Two properties matter and are tested here: a turn that never spoke to UHB says
so instead of showing a plausible complexity, and the summary shown is the
summary - not the messages it replaced.
"""

from __future__ import annotations

import pytest

from uh import decision
from uh.store import Store


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "uh.db") as opened:
        yield opened


@pytest.fixture
def session(store) -> str:
    created = store.create_session(title="decision", model="m", provider="deepseek")
    return created["id"] if isinstance(created, dict) else created


def entry(**overrides) -> dict:
    base = dict(
        provider="deepseek",
        model="deepseek-flash",
        served="deepseek-flash",
        usage={"prompt": 100, "completion": 20, "total": 120},
        duration_ms=900,
        budget=3000,
    )
    base.update(overrides)
    return decision.build_entry(**base)


def route(**overrides) -> dict:
    plan = {
        "mode": "dev",
        "complexity": "trivial",
        "reason": "6 words, no multi-step verb",
        "chosen": "deepseek/deepseek-flash",
        "chain": [
            {"ref": "deepseek/deepseek-flash", "tier": "eco"},
            {"ref": "qwen3:8b", "tier": "free"},
        ],
    }
    plan.update(overrides)
    return plan


# --------------------------------------------------------------------------- #
# recording
# --------------------------------------------------------------------------- #
def test_a_turn_is_recorded_on_the_session(store, session) -> None:
    assert decision.record(store, session, entry()) is True

    stored = store.get_session(session)
    assert stored is not None
    assert stored["metadata"]["last_decision"]["model"] == "deepseek-flash"
    assert len(stored["metadata"]["decisions"]) == 1


def test_only_the_last_ten_turns_are_kept(store, session) -> None:
    for index in range(12):
        decision.record(store, session, entry(duration_ms=index))

    stored = store.get_session(session)
    assert len(stored["metadata"]["decisions"]) == decision.KEEP
    assert stored["metadata"]["decisions"][-1]["duration_ms"] == 11


def test_recording_without_a_session_is_not_an_error(store) -> None:
    assert decision.record(store, None, entry()) is False
    assert decision.record(store, "inconnu", entry()) is False


def test_a_substituted_model_is_recorded_as_such() -> None:
    record = entry(model="alias", served="gpt-oss-120b")

    assert record["substituted"] is True
    assert record["served"] == "gpt-oss-120b"


def test_a_turn_without_counters_says_so() -> None:
    assert entry(usage=None)["usage"]["reported"] is False


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #
def test_an_unknown_session_has_no_payload(store) -> None:
    data = decision.payload(store, "inconnu")

    assert data["available"] is False
    assert data["reason"] == "unknown session"
    assert data["prompt"]["source"] == "unavailable"


def test_a_session_without_turns_explains_itself(store, session) -> None:
    data = decision.payload(store, session)

    assert data["available"] is False
    assert "first answer" in data["reason"]


def test_a_turn_without_uhb_never_invents_a_complexity(store, session) -> None:
    decision.record(store, session, entry())

    data = decision.payload(store, session)

    assert data["available"] is True
    assert data["uhb_consulted"] is False
    assert data["prompt"]["complexity"] is None
    assert data["prompt"]["note"] == decision.NOT_ROUTED
    assert data["chain"] == []
    assert data["prompt"]["source"] == "unavailable"


def test_a_turn_with_uhb_reports_the_chain_in_order(store, session) -> None:
    decision.record(store, session, entry(route=route()))

    data = decision.payload(store, session)

    assert data["uhb_consulted"] is True
    assert data["prompt"]["complexity"] == "trivial"
    assert data["prompt"]["reason"] == "6 words, no multi-step verb"
    assert [item["ref"] for item in data["chain"]] == ["deepseek/deepseek-flash", "qwen3:8b"]
    assert [item["tier"] for item in data["chain"]] == ["eco", "free"]
    assert data["model"]["chosen"] == "deepseek/deepseek-flash"


def test_an_error_from_uhb_is_not_treated_as_a_plan(store, session) -> None:
    decision.record(store, session, entry(route={"error": "unreachable", "kind": "transport"}))

    data = decision.payload(store, session)

    assert data["uhb_consulted"] is False
    assert data["prompt"]["complexity"] is None


def test_the_summary_is_the_summary_not_the_messages(store, session) -> None:
    summary = "12 of 14 earlier exchanges summarised: the user asked about routing."
    decision.record(
        store,
        session,
        entry(summary=summary, summarised_exchanges=12, kept_exchanges=3),
    )

    data = decision.payload(store, session)

    assert data["context"]["summary"] == summary
    assert data["context"]["summarised_exchanges"] == 12
    assert data["context"]["kept_exchanges"] == 3
    assert "12 of 14" in data["context"]["summary"]


def test_the_duration_average_covers_five_turns(store, session) -> None:
    for duration in (1000, 2000, 3000, 4000, 5000, 9000):
        decision.record(store, session, entry(duration_ms=duration))

    data = decision.payload(store, session)

    assert data["turn"]["duration_ms"] == 9000
    assert data["turn"]["turns"] == decision.AVERAGE_WINDOW
    assert data["turn"]["average_ms"] == 4600  # (2000+3000+4000+5000+9000)/5
    assert len(data["turns"]) == decision.AVERAGE_WINDOW


def test_the_payload_carries_the_context_budget(store, session) -> None:
    decision.record(store, session, entry(budget=6000))

    assert decision.payload(store, session)["context"]["budget"] == 6000
