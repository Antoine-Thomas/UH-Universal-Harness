"""Telemetry: the numbers the cockpit shows, computed from what already exists.

No browser and no live machine in these tests: the log parser, the token buckets
and the honesty rules are pure, and the probes are only checked for "returns a
dict or None, never raises".
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest

from uh import telemetry

LOG = """\
2026-11-13 16:29:23  [0s]  ollama: already_up
2026-11-13 16:31:23  [120s]  ok - 0 idea(s), 0 tokens, 120s, routed: ollama/qwen3:8b, /tmp/a/report.md
2026-11-13 16:59:48  [163s]  ok - 2 idea(s), 2063 tokens, 164s, routed: ollama/qwen3:8b, C:\\tmp\\b\\report.md
2026-11-14 03:00:02  [41s]  partial - 1 idea(s), 900 tokens, 41s, routed: deepseek/deepseek-flash, /tmp/c/report.md
2026-11-14 03:10:00  [2s]  uhb start failed: RuntimeError: no route
"""


@pytest.fixture()
def log(tmp_path: Path) -> Path:
    path = tmp_path / "schedule.log"
    path.write_text(LOG, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# schedule.log
# --------------------------------------------------------------------------- #
def test_a_pass_line_is_parsed_into_measurements() -> None:
    parsed = telemetry.parse_pass_line(
        "2026-11-13 16:59:48  [163s]  ok - 2 idea(s), 2063 tokens, 164s, "
        "routed: ollama/qwen3:8b, C:\\tmp\\report.md"
    )

    assert parsed is not None
    assert parsed["at"] == "2026-11-13 16:59:48"
    assert parsed["status"] == "ok"
    assert parsed["ideas"] == 2
    assert parsed["tokens"] == 2063
    assert parsed["wall_time_s"] == 164
    assert parsed["elapsed_s"] == 163
    assert parsed["provider"] == "ollama"
    assert parsed["model"] == "qwen3:8b"
    assert parsed["local"] is True


def test_a_cloud_pass_is_not_local_and_keeps_its_model() -> None:
    parsed = telemetry.parse_pass_line(
        "2026-11-14 03:00:02  [41s]  partial - 1 idea(s), 900 tokens, 41s, "
        "routed: deepseek/deepseek-flash, /tmp/report.md"
    )

    assert parsed is not None
    assert parsed["status"] == "partial"
    assert parsed["provider"] == "deepseek"
    assert parsed["model"] == "deepseek-flash"
    assert parsed["local"] is False


def test_a_diagnostic_line_is_not_a_pass() -> None:
    """`ollama: already_up` is a note about the environment, not a measurement."""
    assert telemetry.parse_pass_line("2026-11-13 16:29:23  [0s]  ollama: already_up") is None
    assert telemetry.parse_pass_line("") is None
    assert telemetry.parse_pass_line("garbage") is None


def test_the_log_counts_passes_and_diagnostics_separately(log: Path) -> None:
    found = telemetry.read_schedule_log(log)

    assert found["lines"] == 5
    assert len(found["passes"]) == 3
    assert found["diagnostics"] == 2


def test_a_missing_log_is_an_empty_log(tmp_path: Path) -> None:
    found = telemetry.read_schedule_log(tmp_path / "nope.log")

    assert found["passes"] == []
    assert found["lines"] == 0
    assert found["diagnostics"] == 0


def test_the_history_is_newest_first_and_limited(log: Path) -> None:
    found = telemetry.dream_history(limit=2, path=log)

    assert [pass_["at"] for pass_ in found["passes"]] == [
        "2026-11-14 03:00:02",
        "2026-11-13 16:59:48",
    ]
    assert found["lines"] == 5


def test_local_means_nobody_is_billed() -> None:
    assert telemetry.is_local("ollama") is True
    assert telemetry.is_local("Ollama") is True
    assert telemetry.is_local("deepseek") is False
    assert telemetry.is_local(None) is False


# --------------------------------------------------------------------------- #
# tokens per day
# --------------------------------------------------------------------------- #
def _session(
    at: Optional[str],
    provider: str,
    total: int,
    reported: bool = True,
    session_day: Optional[str] = None,
) -> dict:
    session: dict = {
        "id": "s-" + (at or "none"),
        "metadata": {
            "decisions": [
                {
                    "provider": provider,
                    "usage": {
                        "prompt": total // 2,
                        "completion": total - total // 2,
                        "total": total,
                        "reported": reported,
                    },
                    "at": at,
                }
            ]
        },
    }
    if session_day:
        session["updated_at"] = f"{session_day}T23:00:00+01:00"
    return session


NOW = datetime.fromisoformat("2026-11-14T12:00:00+01:00")


def test_tokens_are_bucketed_on_the_day_they_were_spent() -> None:
    summary = telemetry.usage_summary(
        [
            _session("2026-11-13T10:00:00+01:00", "ollama", 100),
            _session("2026-11-13T18:00:00+01:00", "deepseek", 400),
            _session("2026-11-14T09:00:00+01:00", "ollama", 50),
        ],
        days=7,
        now=NOW,
    )

    days = {day["date"]: day for day in summary["days"]}
    assert len(summary["days"]) == 7
    assert days["2026-11-13"] == {
        "date": "2026-11-13",
        "local": 100,
        "cloud": 400,
        "turns": 2,
        "estimated": 0,
    }
    assert days["2026-11-14"]["local"] == 50
    assert days["2026-11-12"]["turns"] == 0
    assert summary["totals"] == {
        "local": 150,
        "cloud": 400,
        "tokens": 550,
        "prompt": 275,
        "completion": 275,
        "turns": 3,
        "estimated": 0,
    }


def test_an_old_turn_is_counted_outside_the_window_not_dropped() -> None:
    summary = telemetry.usage_summary(
        [_session("2026-10-01T10:00:00+01:00", "deepseek", 700)], days=7, now=NOW
    )

    assert summary["totals"]["turns"] == 0
    assert summary["outside_window"] == {"turns": 1, "tokens": 700}


def test_an_untimed_turn_lands_on_its_session_day_and_is_marked_estimated() -> None:
    """The tokens were really spent; only the day is inferred - so it is counted
    and flagged, not dropped."""
    summary = telemetry.usage_summary(
        [_session(None, "deepseek", 900, session_day="2026-11-13")], days=7, now=NOW
    )

    day = [bucket for bucket in summary["days"] if bucket["date"] == "2026-11-13"][0]
    assert day["cloud"] == 900
    assert day["estimated"] == 900
    assert summary["totals"]["estimated"] == 900
    assert summary["undated"] == {"turns": 0, "tokens": 0}


def test_a_record_with_no_date_anywhere_stays_out_of_the_chart() -> None:
    summary = telemetry.usage_summary([_session(None, "deepseek", 900)], days=7, now=NOW)

    assert summary["undated"] == {"turns": 1, "tokens": 900}
    assert summary["totals"]["tokens"] == 0
    assert all(day["turns"] == 0 for day in summary["days"])


def test_a_provider_that_reports_nothing_is_a_turn_not_a_zero() -> None:
    summary = telemetry.usage_summary(
        [_session("2026-11-13T10:00:00+01:00", "cloudflare", 0, reported=False)],
        days=7,
        now=NOW,
    )

    assert summary["turns_without_usage"] == 1
    assert summary["totals"]["turns"] == 1
    assert summary["totals"]["tokens"] == 0


def test_cost_has_no_value_and_says_why() -> None:
    """A euro figure from a hardcoded rate is a guess wearing a currency symbol."""
    summary = telemetry.usage_summary([], days=7, now=NOW)

    assert summary["cost"]["value"] is None
    assert "price table" in summary["cost"]["reason"]


def test_a_broken_metadata_blob_does_not_break_the_reading() -> None:
    summary = telemetry.usage_summary(
        [{"id": "x", "metadata": "not a dict"}, {"id": "y"}, {"metadata": {"decisions": "nope"}}],
        days=7,
        now=NOW,
    )

    assert summary["sessions_read"] == 3
    assert summary["totals"]["turns"] == 0


# --------------------------------------------------------------------------- #
# the machine: a probe may answer nothing, never raise
# --------------------------------------------------------------------------- #
def test_the_memory_probe_returns_a_shape_or_none() -> None:
    found = telemetry.memory_usage()

    assert found is None or {"total_gb", "used_gb", "percent"} <= set(found)


def test_the_snapshot_always_has_its_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two slow probes are replaced: the shape is what this pins."""
    monkeypatch.setattr(telemetry, "cpu_percent", lambda sample=0.12: 12.5)
    monkeypatch.setattr(telemetry, "memory_usage", lambda: {"total_gb": 32.0, "used_gb": 8.0, "percent": 25.0})
    monkeypatch.setattr(telemetry, "vram_usage", lambda ttl=5.0: None)

    found = telemetry.system_snapshot()

    assert found["cpu_percent"] == 12.5
    assert found["ram"]["percent"] == 25.0
    assert found["vram"] is None
    assert found["cores"] >= 1
    assert found["platform"]


def test_the_whole_payload_carries_the_three_readings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))  # never read the real home
    payload = telemetry.snapshot([], days=7)

    assert set(payload) == {"generated_at", "window_days", "usage", "system", "dreams"}
    assert payload["window_days"] == 7
    assert len(payload["usage"]["days"]) == 7
    assert "passes" in payload["dreams"]
