"""Windows copy tools: the destination is not always the last argument.

cp/mv put the destination last, mklink creates its *first* argument and robocopy
takes the destination *second*. Each one is checked where it actually writes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import sandbox
from uh.toolsets.terminal import TerminalToolset, check_writes

SYSTEM_TARGET = "C:/Windows" if os.name == "nt" else "/etc"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "home"
    for name in sandbox.ALLOWED_HOME_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: root))
    monkeypatch.setattr(
        os.path, "expanduser", lambda value: str(root) + value[1:] if value.startswith("~") else value
    )
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
# Windows copy tools: destination position differs from cp
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "command",
    ["xcopy a.txt b.txt", "robocopy . backup", "mklink lien.txt cible.txt"],
)
def test_windows_copy_destinations_inside_the_workspace_are_allowed(
    toolset: TerminalToolset, command: str
) -> None:
    assert check_writes(command, toolset.workspace, toolset.allowed) is None



@pytest.mark.parametrize(
    "command",
    [
        f"xcopy a.txt {SYSTEM_TARGET}/x",
        f"robocopy . {SYSTEM_TARGET}/x",
        f"mklink {SYSTEM_TARGET}/lien.txt cible.txt",
    ],
)
def test_windows_copy_destinations_outside_are_refused(
    toolset: TerminalToolset, command: str
) -> None:
    refusal = check_writes(command, toolset.workspace, toolset.allowed)

    assert refusal is not None, command
    assert refusal["refused"] is True
