"""Smoke tests - Jalon 0 only checks that the CLI runs and exits cleanly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    # The CLI reconfigures its stdio to UTF-8, so read its output as UTF-8 -
    # decoding with the Windows locale (cp1252) kills the reader thread and
    # leaves stdout as None.
    return subprocess.run(
        [sys.executable, "-m", "uh", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_version_exits_zero() -> None:
    result = _run("--version")
    assert result.returncode == 0, result.stderr
    assert "uh " in (result.stdout + result.stderr)


def test_doctor_exits_zero() -> None:
    result = _run("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "doctor" in result.stdout
    assert "python" in result.stdout


def test_group_without_action_exits_two() -> None:
    # Jalon 3 removed the last placeholder command: the exit-2 contract now lives
    # on the "group without an action" path, which prints that group's help.
    result = _run("sessions")
    assert result.returncode == 2
    assert "usage: uh sessions" in (result.stdout + result.stderr)
