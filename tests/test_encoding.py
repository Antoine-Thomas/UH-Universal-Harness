"""Portability: a cp1252 console must not crash on non-ASCII output."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(
    args: list[str], *, encoding_env: str | None = None, home: Path | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # Never the developer's real ~/.uh. `uh doctor` opens the store, and a clean
    # close checkpoints the WAL: pointed at a live home it rewrites uh.db and
    # deletes uh.db-wal/uh.db-shm, which the session guard reports as an
    # isolation leak (and which can cost a running session its journal).
    env["UH_HOME"] = str(home or Path(tempfile.mkdtemp(prefix="uh-encoding-")))
    if encoding_env:
        env["PYTHONIOENCODING"] = encoding_env
    return subprocess.run(
        [sys.executable, "-m", "uh", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        env=env,
    )


def test_help_survives_cp1252_console() -> None:
    result = _run(["--help"], encoding_env="cp1252:strict")
    assert result.returncode == 0, result.stderr
    assert "usage: uh" in result.stdout


def test_version_survives_cp1252_console() -> None:
    from uh import __version__

    result = _run(["--version"], encoding_env="cp1252:strict")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"uh {__version__}"


def test_doctor_survives_cp1252_console() -> None:
    result = _run(["doctor"], encoding_env="cp1252:strict")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "doctor" in result.stdout


def test_non_ascii_output_does_not_raise(tmp_path: Path) -> None:
    """After ``_configure_stdio`` a strict cp1252 stream accepts accents and box drawing."""
    script = (
        "import sys\n"
        "from uh.cli import _configure_stdio\n"
        "from uh.banner import LOGO_UNICODE\n"
        "_configure_stdio()\n"
        "print('accents: éàü and box: ██╗')\n"
        "print(LOGO_UNICODE)\n"
    )
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252:strict"
    env["UH_HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "accents" in result.stdout


def test_the_spawned_cli_never_points_at_the_real_uh_home(
    monkeypatch, tmp_path: Path
) -> None:
    """A guard: `uh doctor` on the real ~/.uh checkpoints and deletes its WAL."""
    seen: dict = {}

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return Done()

    monkeypatch.setattr(subprocess, "run", fake_run)
    _run(["--version"], home=tmp_path / "home")

    assert seen["env"]["UH_HOME"] == str(tmp_path / "home")
    assert seen["env"]["UH_HOME"] != str(Path.home() / ".uh")
