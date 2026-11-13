"""The nightly task's interpreter, and the XML that carries wake/battery settings.

Kept apart from ``test_dream_schedule.py`` on purpose: that file exercises the
Startup fallback, this one the Task Scheduler definition - two mechanisms, two
failure modes, and a test that mixes them cannot say which one broke.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uh import dream_schedule


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "uh-home"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(dream_schedule.os, "name", "nt")
    monkeypatch.delenv(dream_schedule.ENV_INTERPRETER, raising=False)
    return tmp_path


def _fake_python(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    return path


# --------------------------------------------------------------------------- #
# which interpreter the task may use
# --------------------------------------------------------------------------- #
def test_a_tool_venv_is_never_scheduled(tmp_path: Path) -> None:
    """The agent's venv is rebuilt by every update; the task must not live there."""
    venv_python = _fake_python(tmp_path / "hermes-agent" / "venv" / "Scripts" / "python.exe")
    (venv_python.parent.parent / "pyvenv.cfg").write_text(
        "home = C:\\Python310", encoding="ascii"
    )
    system = _fake_python(tmp_path / "Programs" / "Python" / "Python310" / "python.exe")

    chosen = dream_schedule.resolve_interpreter(
        candidates=[str(venv_python), str(system)], probe=lambda exe: True
    )

    assert chosen == str(system)


def test_the_interpreter_must_import_uh(tmp_path: Path) -> None:
    without = _fake_python(tmp_path / "Python311" / "python.exe")
    with_uh = _fake_python(tmp_path / "Python310" / "python.exe")

    chosen = dream_schedule.resolve_interpreter(
        candidates=[str(without), str(with_uh)], probe=lambda exe: exe == str(with_uh)
    )

    assert chosen == str(with_uh)


def test_an_explicit_interpreter_may_be_a_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UH_PYTHON is the escape hatch: naming a venv makes it acceptable."""
    venv_python = _fake_python(tmp_path / "venv" / "Scripts" / "python.exe")
    (venv_python.parent.parent / "pyvenv.cfg").write_text("home = .", encoding="ascii")
    monkeypatch.setenv(dream_schedule.ENV_INTERPRETER, str(venv_python))

    chosen = dream_schedule.resolve_interpreter(probe=lambda exe: True)

    assert chosen == str(venv_python)


def test_no_usable_interpreter_refuses_rather_than_schedules_it(tmp_path: Path) -> None:
    system = _fake_python(tmp_path / "Python311" / "python.exe")

    with pytest.raises(dream_schedule.ScheduleError) as caught:
        dream_schedule.resolve_interpreter(candidates=[str(system)], probe=lambda exe: False)

    assert "cannot import uh" in str(caught.value)


def test_the_probe_ignores_this_shells_pythonpath(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task inherits no PYTHONPATH: a probe that honoured it proves nothing."""
    monkeypatch.setenv("PYTHONPATH", r"C:\repo")
    monkeypatch.setenv("VIRTUAL_ENV", r"C:\venv")

    env = dream_schedule._probe_env()

    assert "PYTHONPATH" not in env
    assert "VIRTUAL_ENV" not in env


# --------------------------------------------------------------------------- #
# the task definition
# --------------------------------------------------------------------------- #
def test_the_task_xml_wakes_the_machine_and_runs_on_battery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xml.etree import ElementTree

    monkeypatch.setattr(dream_schedule, "_task_user", lambda: "S-1-5-21-1")
    xml = dream_schedule.task_xml(
        r"C:\Python310\python.exe", dream_schedule.task_arguments(), "03:00"
    )
    root = ElementTree.fromstring(xml)
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

    assert root.findtext("t:Settings/t:WakeToRun", namespaces=ns) == "true"
    assert root.findtext("t:Settings/t:DisallowStartIfOnBatteries", namespaces=ns) == "false"
    assert root.findtext("t:Settings/t:StopIfGoingOnBatteries", namespaces=ns) == "false"
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=ns) == r"C:\Python310\python.exe"
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) or ""
    assert "-m uh.cli" in arguments, "without the module flag, python runs `dream` as a file"
    assert "--start-uhb" in arguments, "without UHB up, the pass skips every night"


def test_install_registers_from_xml_not_from_tr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pinned = _fake_python(tmp_path / "Programs" / "Python" / "Python310" / "python.exe")
    monkeypatch.setattr(dream_schedule, "_system_pythons", lambda: [str(pinned)])
    monkeypatch.setattr(dream_schedule, "_has_uh", lambda exe: True)
    monkeypatch.setattr(dream_schedule, "_task_user", lambda: "S-1-5-21-1")
    calls: list = []

    def ok(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dream_schedule.subprocess, "run", ok)

    installed = dream_schedule.install(at="03:00")

    create = [call for call in calls if "/create" in call]
    assert create, "the task was never created"
    assert "/xml" in create[0], "only XML carries WakeToRun and the battery flags"
    assert "/tr" not in create[0]
    assert installed["interpreter"] == str(pinned)
