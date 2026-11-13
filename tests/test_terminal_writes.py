"""The terminal toolset refuses the obvious writes that left the sandbox.

The incident: a model whose `write_file` was refused created the directory with
`terminal` instead. The destination check now runs before the shell sees the
line, so a refused command creates nothing.

Scope, stated in the module docstring: redirections and the handful of commands
whose arguments are paths. A program that writes by itself is out of reach.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import sandbox
from uh.toolsets import terminal as terminal_mod
from uh.toolsets.terminal import TerminalToolset, check_writes

SYSTEM_TARGET = "C:/Windows" if os.name == "nt" else "/etc"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    for name in sandbox.ALLOWED_HOME_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: root))
    # ~ must resolve inside the fake home too: Path.expanduser() goes through
    # os.path.expanduser, which reads HOME/USERPROFILE and ignores Path.home().
    monkeypatch.setattr(
        os.path, "expanduser", lambda value: str(root) + value[1:] if value.startswith("~") else value
    )

    def fake_expanduser(self: Path) -> Path:
        # Patching the method itself keeps the test independent of how a given
        # pathlib implements expanduser (3.10 goes through os.path, later
        # versions parse the parts).
        text = str(self).replace("\\", "/")  # Path("~/x") renders as "~\x" on Windows
        if text == "~":
            return root
        if text.startswith("~/"):
            return root / text[2:]
        return self

    monkeypatch.setattr(Path, "expanduser", fake_expanduser)
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "projet"
    target.mkdir()
    return target


@pytest.fixture
def toolset(workspace: Path, home: Path) -> TerminalToolset:
    return TerminalToolset(workspace)


# --------------------------------------------------------------------------- #
# allowed destinations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "command",
    [
        "echo hi > note.txt",
        "echo hi > sub/note.txt",
        "echo hi >> journal.txt",
        "mkdir build",
        "mkdir -p a/b/c",
        "touch src.txt",
        "cp a.txt b.txt",
        "echo hi > /dev/null",
        "ls 2>&1",
    ],
)
def test_commands_inside_the_workspace_are_not_refused(
    toolset: TerminalToolset, command: str
) -> None:
    assert check_writes(command, toolset.workspace, toolset.allowed) is None


@pytest.mark.parametrize(
    "command",
    [
        "echo hi > ~/Desktop/note.txt",
        "touch ~/Documents/note.txt",
        "mkdir -p ~/uh-workspace/brouillon",
        "cp a.txt ~/Downloads/a.txt",
    ],
)
def test_whitelisted_destinations_are_not_refused(
    toolset: TerminalToolset, command: str, home: Path
) -> None:
    assert check_writes(command, toolset.workspace, toolset.allowed) is None


@pytest.mark.parametrize(
    "command",
    [
        'echo "a > b"',  # quoted: not a redirect
        'git commit -m "mkdir /etc"',  # another program's argument
        "python -c \"print('mkdir /etc')\"",
        "pip install uh-harness",
        "ls -la",
    ],
)
def test_text_that_looks_like_a_write_is_not_refused(
    toolset: TerminalToolset, command: str
) -> None:
    assert check_writes(command, toolset.workspace, toolset.allowed) is None


# --------------------------------------------------------------------------- #
# refused destinations
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "command",
    [
        f"mkdir {SYSTEM_TARGET}/evil",
        f"echo x > {SYSTEM_TARGET}/evil.txt",
        f"echo x >> {SYSTEM_TARGET}/evil.txt",
        f"echo x>{SYSTEM_TARGET}/evil-nospace.txt",
        f"echo err 2> {SYSTEM_TARGET}/err.txt",
    ],
)
def test_a_system_destination_is_refused_as_a_system_path(
    toolset: TerminalToolset, command: str
) -> None:
    refusal = check_writes(command, toolset.workspace, toolset.allowed)

    assert refusal is not None, command
    assert refusal["refused"] is True
    assert "is a system path, which UH never writes to" in refusal["error"]


# --------------------------------------------------------------------------- #
# deletions follow the same rule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "command",
    [
        "rm ./note.txt",
        "rm build/out.bin",
        "rmdir vieux",
        "del note.txt",
        "del /Q brouillon.txt",
        "truncate -s 0 note.txt",
        "rm ~/Desktop/vieux.txt",
    ],
)
def test_deletions_inside_the_sandbox_are_not_refused(
    toolset: TerminalToolset, command: str
) -> None:
    assert check_writes(command, toolset.workspace, toolset.allowed) is None


@pytest.mark.parametrize(
    "command",
    [
        f"rm {SYSTEM_TARGET}/foo.dll",
        f"del /S {SYSTEM_TARGET}/x.dll",
        "rm ~/secret.txt",
        "rm ../dehors.txt",
        "rmdir /Q ~/secret",
        "erase ../dehors.txt",
        "unlink ~/secret.txt",
        f"truncate -s 0 {SYSTEM_TARGET}/conf",
    ],
)
def test_deletions_outside_the_sandbox_are_refused(
    toolset: TerminalToolset, command: str
) -> None:
    refusal = check_writes(command, toolset.workspace, toolset.allowed)

    assert refusal is not None, command
    assert refusal["refused"] is True


def test_a_refused_deletion_never_reaches_the_shell(
    toolset: TerminalToolset, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        terminal_mod.subprocess, "run", lambda *a, **k: pytest.fail("shell spawned")
    )

    result = toolset.terminal(f"rm {SYSTEM_TARGET}/critical.dll")

    assert result["refused"] is True


@pytest.mark.parametrize(
    "command",
    [
        "touch ~/secret.txt",
        "mv a.txt ../dehors.txt",
        "cp a.txt /tmp/evil.txt",
    ],
)
def test_destinations_outside_both_lists_are_refused(
    toolset: TerminalToolset, command: str
) -> None:
    refusal = check_writes(command, toolset.workspace, toolset.allowed)

    assert refusal is not None, command
    assert refusal["refused"] is True
    assert "outside the workspace and outside the allowed paths" in refusal["error"]
    assert "~/Desktop" in refusal["error"]
    assert "(hint: /workspace <path> or `uh --workspace <path>`)" in refusal["error"]


def test_a_refused_command_never_reaches_the_shell(
    toolset: TerminalToolset, monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess was spawned for a refused write")

    monkeypatch.setattr(terminal_mod.subprocess, "run", forbidden)
    target = workspace / ".." / "dehors" / "note.txt"

    result = toolset.terminal(f'echo x > "{target}"')

    assert result["refused"] is True
    assert not target.exists()


def test_the_blacklist_still_wins(toolset: TerminalToolset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal_mod.subprocess, "run", lambda *a, **k: pytest.fail("spawned"))
    result = toolset.terminal("rm -rf /")
    assert "blacklist" in result["error"]


def test_a_program_that_writes_by_itself_is_a_documented_gap(
    toolset: TerminalToolset, workspace: Path
) -> None:
    """Not a sandbox: `python -c` is out of reach, and the docstring says so."""
    command = "python -c \"open('escape.txt','w').write('x')\""
    assert check_writes(command, toolset.workspace, toolset.allowed) is None
    assert "not a sandbox" in (terminal_mod.__doc__ or "").lower()
