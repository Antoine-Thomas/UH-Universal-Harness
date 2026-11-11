"""Smoke tests - Jalon 0 only checks that the CLI runs and exits cleanly."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "uh", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
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


def test_placeholder_exits_two() -> None:
    result = _run("model", "list")
    assert result.returncode == 2
    assert "Not implemented in Jalon 0" in (result.stdout + result.stderr)
