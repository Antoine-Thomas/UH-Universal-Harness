"""Evaluation table: what each engine actually did, measured.

"Faster" is not "smarter", so the panel needs numbers per model: turns, average
latency, tokens per second and how often a tool was really called.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from uh import telemetry


def decision(
    provider: str,
    model: str,
    *,
    duration_ms: int,
    tokens: int,
    tools: int = 0,
    at: str = "",
    served: str = "",
) -> Dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "served": served or model,
        "duration_ms": duration_ms,
        "tool_calls": tools,
        "usage": {"total": tokens, "reported": True},
        "substituted": bool(served and served != model),
        "at": at or datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def session(*decisions: Dict[str, Any]) -> Dict[str, Any]:
    return {"metadata": {"decisions": list(decisions)}}


def test_two_turns_of_one_model_are_averaged() -> None:
    payload = telemetry.model_breakdown(
        [session(decision("ollama", "lfm3", duration_ms=2000, tokens=100, tools=1),
                 decision("ollama", "lfm3", duration_ms=4000, tokens=300, tools=0))]
    )

    (row,) = payload["models"]
    assert row["model"] == "lfm3"
    assert row["turns"] == 2
    assert row["avg_ms"] == 3000
    assert row["tokens"] == 400
    assert row["tokens_per_s"] == 66.7, "400 tokens over 6 s of generation"
    assert row["tool_calls"] == 1
    assert row["local"] is True


def test_two_engines_stay_two_rows() -> None:
    payload = telemetry.model_breakdown(
        [
            session(
                decision("ollama", "lfm3", duration_ms=1000, tokens=50),
                decision("deepseek", "deepseek-v4-pro", duration_ms=1000, tokens=500),
            )
        ]
    )

    models = {row["model"]: row for row in payload["models"]}
    assert set(models) == {"lfm3", "deepseek-v4-pro"}
    assert models["deepseek-v4-pro"]["local"] is False
    # The order is by volume of use, so the row that matters most comes first.
    assert payload["models"][0]["turns"] == payload["models"][1]["turns"] == 1


def test_a_served_substitution_is_counted_under_the_real_model() -> None:
    payload = telemetry.model_breakdown(
        [session(decision("router", "asked", served="served", duration_ms=1000, tokens=10))]
    )
    (row,) = payload["models"]
    assert row["model"] == "served"
    assert row["substituted"] == 1


def test_an_undated_turn_is_reported_not_guessed() -> None:
    payload = telemetry.model_breakdown(
        [session({"provider": "ollama", "model": "lfm3", "duration_ms": 10, "usage": {}})]
    )
    assert payload["models"] == []
    assert payload["undated"] == 1


def test_a_turn_outside_the_window_is_not_attributed_to_today() -> None:
    old = (datetime.now().astimezone() - timedelta(days=30)).isoformat(timespec="seconds")
    payload = telemetry.model_breakdown(
        [session(decision("ollama", "lfm3", duration_ms=10, tokens=1, at=old))], days=7
    )
    assert payload["models"] == []
    assert payload["outside_window"] == 1


def test_an_empty_history_is_an_empty_table() -> None:
    payload = telemetry.model_breakdown([])
    assert payload == {"models": [], "undated": 0, "outside_window": 0}
