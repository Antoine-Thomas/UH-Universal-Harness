"""uh upgrade: venv guard, pip invocation, version reporting, --restart.

No network and no real pip run: ``subprocess.run`` and ``os.execv`` are stubbed
and the metadata read is driven by a scripted list, so a bug here cannot
rewrite the interpreter that runs the suite.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from uh import cli


def _args(**overrides) -> SimpleNamespace:
    defaults = dict(restart=False, user=False, dry_run=False, debug=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _completed(code: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["pip"], returncode=code)


@pytest.fixture
def no_exec(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record restarts instead of replacing the test process."""
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.os, "execv", lambda path, argv: calls.append([path, *argv]))
    return calls


def _versions(monkeypatch: pytest.MonkeyPatch, *values: str | None) -> None:
    script = list(values)
    monkeypatch.setattr(cli, "installed_version", lambda: script.pop(0) if script else None)
    monkeypatch.setattr(cli, "__version__", "1.0.0")


def test_refuses_outside_a_virtualenv_before_running_pip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: False)
    _versions(monkeypatch, "1.0.0")
    ran: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: ran.append(cmd))

    assert cli.cmd_upgrade(_args()) == 1

    assert not ran, "pip must never run outside a venv without --user"
    assert "virtualenv" in capsys.readouterr().err


def test_user_flag_allows_a_system_interpreter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_exec
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: False)
    _versions(monkeypatch, "1.0.0", "1.0.0")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or _completed())

    assert cli.cmd_upgrade(_args(user=True)) == 0

    assert seen and "--user" in seen[0]
    assert "already up to date" in capsys.readouterr().out
    assert not no_exec


def test_inside_a_venv_the_command_is_pip_install_upgrade(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: True)
    _versions(monkeypatch, "1.0.0", "1.0.0")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or _completed())

    assert cli.cmd_upgrade(_args()) == 0

    assert seen[0][1:] == ["-m", "pip", "install", "--upgrade", "uh-harness"]
    assert "--user" not in seen[0]
    assert "already up to date (1.0.0)" in capsys.readouterr().out


def test_dry_run_passes_the_flag_and_installs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_exec
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: True)
    _versions(monkeypatch, "1.0.0")
    seen: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or _completed())

    assert cli.cmd_upgrade(_args(dry_run=True, restart=True)) == 0

    assert "--dry-run" in seen[0]
    out = capsys.readouterr().out
    assert "dry run" in out
    assert not no_exec, "--dry-run must never restart"


def test_pip_failure_keeps_the_installed_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_exec
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: True)
    _versions(monkeypatch, "1.0.0")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: _completed(code=2))

    assert cli.cmd_upgrade(_args(restart=True)) == 2

    captured = capsys.readouterr()
    assert "pip failed" in captured.err
    assert "stays installed" in captured.err
    assert not no_exec, "a failed upgrade must not restart uh"


def test_pip_that_cannot_start_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: True)
    _versions(monkeypatch, "1.0.0")

    def boom(cmd, **kw):
        raise OSError("no pip here")

    monkeypatch.setattr(cli.subprocess, "run", boom)

    assert cli.cmd_upgrade(_args()) == 1
    assert "cannot run pip" in capsys.readouterr().err


def test_upgrade_without_restart_prints_the_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_exec
) -> None:
    monkeypatch.setattr(cli, "in_virtualenv", lambda: True)
    _versions(monkeypatch, "1.0.0", "1.0.1")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: _completed())

    assert cli.cmd_upgrade(_args()) == 0

    out = capsys.readouterr().out
    assert "1.0.0 -> 1.0.1" in out
    assert "relaunch uh to use the new version" in out
    assert not no_exec


def test_restart_re_execs_the_console_module(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_exec
) -> None:
    import sys

    monkeypatch.setattr(cli, "in_virtualenv", lambda: True)
    _versions(monkeypatch, "1.0.0", "1.0.1")
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, **kw: _completed())

    assert cli.cmd_upgrade(_args(restart=True)) == 0

    assert no_exec == [[sys.executable, sys.executable, "-m", "uh"]]
    assert "1.0.1" in capsys.readouterr().out


def test_upgrade_is_wired_into_the_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    monkeypatch.setattr(cli, "cmd_upgrade", lambda args: seen.append(args) or 0)

    assert cli.main(["upgrade", "--dry-run"]) == 0

    assert seen and seen[0].dry_run is True  # type: ignore[attr-defined]
    assert seen[0].restart is False  # type: ignore[attr-defined]
