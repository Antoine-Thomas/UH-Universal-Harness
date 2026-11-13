"""Scheduling the nightly pass: Task Scheduler when allowed, Startup always.

The interesting cases are the ones that fail here: this machine is not admin, so
the Task Scheduler branch is exercised as a *refusal* and the Startup fallback as
the path that must actually work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uh import dream_schedule


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "uh-home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(dream_schedule.os, "name", "nt")
    return tmp_path


# --------------------------------------------------------------------------- #
# parsing and the script
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", ["03:00", "0:00", "23:59", " 7:05 "])
def test_a_wall_time_is_accepted(value: str) -> None:
    hour, minute = dream_schedule.parse_at(value)
    assert 0 <= hour <= 23 and 0 <= minute <= 59


@pytest.mark.parametrize("value", ["3", "25:00", "03:60", "nope", ""])
def test_anything_else_is_refused(value: str) -> None:
    with pytest.raises(dream_schedule.ScheduleError):
        dream_schedule.parse_at(value)


def test_the_next_run_is_today_or_tomorrow() -> None:
    from datetime import datetime

    assert dream_schedule.next_run("03:00", now=datetime(2026, 11, 13, 1, 0)).startswith(
        "2026-11-13T03:00"
    )
    assert dream_schedule.next_run("03:00", now=datetime(2026, 11, 13, 4, 0)).startswith(
        "2026-11-14T03:00"
    )


def test_the_script_compares_a_target_instead_of_a_single_minute(tmp_path: Path) -> None:
    """Hour(Now)=3 And Minute(Now)=0 misses the night on any clock drift."""
    text = dream_schedule.vbs_text("uh dream --now", "03:00", tmp_path / "schedule.log")

    assert "DateAdd" in text
    assert "Do While Now < target" in text
    assert "WScript.Sleep 30000" in text
    assert "Hour(Now) = 3" not in text, "a one-minute window loses the night"
    assert "86400000" not in text, "a blind 24h sleep loses a night on any drift"


def test_the_script_writes_its_report_and_its_log(tmp_path: Path) -> None:
    text = dream_schedule.vbs_text(
        dream_schedule.pass_command(), "03:00", tmp_path / "schedule.log"
    )

    assert "dream --now" in text
    assert "--no-write" not in text, "a nightly pass that writes nothing leaves no trace"
    assert "--budget" in text
    assert "schedule.log" in text
    assert text.isascii(), "VBScript reads no BOM as the system codepage"


# --------------------------------------------------------------------------- #
# installation
# --------------------------------------------------------------------------- #
def test_without_admin_the_startup_fallback_is_used(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"Acces refuse")
    calls: list = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return refused

    # monkeypatch, not a hand-rolled try/finally: assigning back
    # `subprocess.run = subprocess.run` after the patch restores the patched
    # function to itself, and every later subprocess call in the session gets
    # the fake (which is how ten unrelated tests started failing).
    monkeypatch.setattr(dream_schedule.subprocess, "run", fake_run)
    installed = dream_schedule.install(at="03:00")

    assert installed["mechanism"] == "startup"
    assert "requires admin" in installed["detail"]
    script = Path(str(installed["path"]))
    assert script.is_file()
    assert script.name == dream_schedule.VBS_NAME
    assert "Startup" in str(script)


def test_schtasks_success_is_preferred(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def ok(argv, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dream_schedule.subprocess, "run", ok)

    installed = dream_schedule.install(at="04:30")

    assert installed["mechanism"] == "schtasks"
    assert "04:30" in installed["detail"]
    assert installed["path"] is None


def test_install_refuses_a_bad_hour(home: Path) -> None:
    with pytest.raises(dream_schedule.ScheduleError):
        dream_schedule.install(at="3am")


def test_status_says_nothing_is_installed_at_first(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dream_schedule, "task_exists", lambda: False)

    state = dream_schedule.status()

    assert state["installed"] is False
    assert state["mechanism"] is None
    assert state["last_pass"] is None
    assert state["next_run"] is None


def test_status_reports_the_startup_script_once_installed(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dream_schedule, "task_exists", lambda: False)
    folder = dream_schedule.startup_dir()
    assert folder is not None
    folder.mkdir(parents=True, exist_ok=True)
    (folder / dream_schedule.VBS_NAME).write_text("' x", encoding="ascii")

    state = dream_schedule.status(at="03:00")

    assert state["installed"] is True
    assert state["mechanism"] == "startup"
    assert state["next_run"]


def test_status_reports_the_last_pass(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from uh.dream import dreams_dir

    monkeypatch.setattr(dream_schedule, "task_exists", lambda: False)
    passes = dreams_dir() / "passes"
    for name in ("20261112-030000", "20261113-030000"):
        (passes / name).mkdir(parents=True)

    assert dream_schedule.last_pass() == "20261113-030000"


def test_uninstall_removes_the_script(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dream_schedule, "task_exists", lambda: False)

    def refused(argv, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=1, stdout=b"", stderr=b"")

    # Refused for the whole test: install() must land on the Startup script,
    # which is the only mechanism an uninstall has something to remove.
    monkeypatch.setattr(dream_schedule.subprocess, "run", refused)
    script = Path(str(dream_schedule.install()["path"]))
    assert script.is_file()

    removed = dream_schedule.uninstall()["removed"]

    assert str(script) in removed
    assert not script.is_file()


def test_uninstall_reports_nothing_when_nothing_is_there(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(argv, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(dream_schedule.subprocess, "run", refused)

    assert dream_schedule.uninstall()["removed"] == []


# --------------------------------------------------------------------------- #
# the CLI
# --------------------------------------------------------------------------- #
def test_the_cli_installs_and_reports_the_mechanism(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from uh import cli

    def refused(argv, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(dream_schedule.subprocess, "run", refused)

    code = cli.main(["dream", "--schedule", "--at", "03:00"])

    out = capsys.readouterr().out
    assert code == 0
    assert "requires admin" in out
    assert "script:" in out
    assert "command:" in out


def test_the_cli_status_and_uninstall(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from uh import cli

    monkeypatch.setattr(dream_schedule, "task_exists", lambda: False)

    def refused(argv, **kwargs):
        return subprocess.CompletedProcess(args=argv, returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(dream_schedule.subprocess, "run", refused)
    assert cli.main(["dream", "--schedule"]) == 0
    capsys.readouterr()

    assert cli.main(["dream", "--status"]) == 0
    assert "installed : True" in capsys.readouterr().out

    assert cli.main(["dream", "--uninstall"]) == 0
    assert "removed" in capsys.readouterr().out
    assert dream_schedule.status()["installed"] is False
