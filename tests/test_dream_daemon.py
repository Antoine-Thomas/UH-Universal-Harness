"""The daemon decides *when* to dream; these tests pin that decision.

No clock, no sleeping, no OS call: the loop takes its tick, its idle reading and
its pass runner as arguments, so a fifteen-minute pause can be simulated in a
microsecond - and the rules can be read instead of guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import dream_daemon


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    monkeypatch.delenv(dream_daemon.IDLE_ENV, raising=False)


def _run(idle_values, monkeypatch, *, max_ticks=None, runner=None):
    values = list(idle_values)
    seen: list = []

    def fake_idle():
        return values.pop(0) if values else 0.0

    def fake_sleep(_seconds):
        seen.append("slept")

    def pass_runner():
        seen.append("dreamed")
        if runner is not None:
            runner()

    return dream_daemon.run(
        pass_runner,
        tick=0.0,
        sleep=fake_sleep,
        now=lambda: 0.0,
        idle=fake_idle,
        max_ticks=max_ticks,
    ), seen


def test_the_threshold_is_fifteen_minutes_by_default() -> None:
    assert dream_daemon.idle_minutes() == 15.0


def test_the_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dream_daemon.IDLE_ENV, "3")
    assert dream_daemon.idle_minutes() == 3.0


def test_a_broken_value_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dream_daemon.IDLE_ENV, "beaucoup")
    assert dream_daemon.idle_minutes() == 15.0


def test_nothing_happens_while_the_operator_is_working(monkeypatch: pytest.MonkeyPatch) -> None:
    result, seen = _run([10.0, 30.0, 600.0], monkeypatch, max_ticks=3)

    assert "dreamed" not in seen, "30 s of silence is not a pause"
    assert result["passes"] == 0


def test_a_long_pause_triggers_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """One absence, one pass - and the daemon waits for real input afterwards."""
    state = {"idle": 901.0}
    dreams: list = []

    def pass_runner():
        dreams.append("dreamed")
        state["idle"] = 0.0  # the operator comes back while the pass runs

    result = dream_daemon.run(
        pass_runner,
        tick=0.0,
        sleep=lambda _s: None,
        now=lambda: 0.0,
        idle=lambda: state["idle"],
        max_ticks=4,
    )

    assert dreams == ["dreamed"]
    assert result["passes"] == 1


def test_an_unknown_idle_time_dreams_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon that cannot tell whether you are there must not guess."""
    result, seen = _run([None, None], monkeypatch, max_ticks=2)

    assert "dreamed" not in seen
    # A clean exit says "stopped", and remembers that it stopped because the API
    # could not tell whether anyone was there.
    assert dream_daemon.read_status()["last_state"] == "unsupported"


def test_a_failing_pass_does_not_kill_the_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("model down")

    result, seen = _run([901.0, 0.0], monkeypatch, max_ticks=2, runner=boom)

    assert "dreamed" in seen
    assert result["passes"] == 0, "a failed pass is not a dreamed pass"
    assert "failed" in str(result["last_pass"])


def test_the_stop_file_ends_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    dream_daemon.stop()

    result, seen = _run([901.0], monkeypatch, max_ticks=5)

    assert result["passes"] == 0
    assert not dream_daemon.running(), "the stop file is cleared on a clean exit"


def test_the_status_file_says_what_is_happening(monkeypatch: pytest.MonkeyPatch) -> None:
    dream_daemon.write_status(state="dreaming", passes=2)
    status = dream_daemon.read_status()

    assert status["state"] == "dreaming"
    assert status["passes"] == "2"


def test_idle_seconds_is_none_where_the_api_does_not_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Off Windows - and on any CI runner - the answer is "unknown", not zero."""
    monkeypatch.delattr(dream_daemon.ctypes, "windll", raising=False)

    assert dream_daemon.idle_seconds() is None


def test_low_priority_is_attempted_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(dream_daemon.ctypes, "windll", raising=False)

    assert dream_daemon.low_priority() is False, "no ctypes, no crash"
