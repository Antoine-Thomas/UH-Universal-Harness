"""/workspace: show where UH writes, and move the session when asked.

The command moves the session and every filesystem toolset. ``memory`` stays in
``$UH_HOME`` on purpose - ``MEMORY.md`` is UH's own state, not project data.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import commands, console
from uh import config as uh_config
from uh.toolsets.file import FileToolset
from uh.toolsets.memory import MemoryToolset
from uh.toolsets.terminal import TerminalToolset

# The directory must exist on the platform running the tests: /workspace
# refuses a missing path before it can refuse a system one.
SYSTEM_CWD = Path("C:/Windows/System32") if os.name == "nt" else Path("/usr/bin")


def make_session(tmp_path: Path, workspace: Path) -> console.ChatSession:
    toolsets = {
        "file": FileToolset(workspace),
        "terminal": TerminalToolset(workspace),
        "memory": MemoryToolset(home=tmp_path / "home"),
    }
    return console.ChatSession(
        provider_factory=lambda kind, model: None,  # type: ignore[arg-type,return-value]
        provider_kind="stub",
        chain=[],
        model="stub:1b",
        toolsets=toolsets,
        workspace=workspace,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=1,
    )


@pytest.fixture
def session(tmp_path: Path) -> console.ChatSession:
    workspace = tmp_path / "projet"
    workspace.mkdir()
    return make_session(tmp_path, workspace)


def test_the_command_shows_the_workspace_and_the_allowed_paths(
    session: console.ChatSession,
) -> None:
    result = commands.dispatch(session, "/workspace")

    assert result.err == ""
    assert str(session.workspace) in result.out
    assert "~/Desktop" in result.out and "~/uh-workspace" in result.out
    assert "usage: /workspace <path>" in result.out


def test_switching_moves_the_session_and_both_filesystem_toolsets(
    session: console.ChatSession, tmp_path: Path
) -> None:
    target = tmp_path / "autre-projet"
    target.mkdir()

    result = commands.dispatch(session, f"/workspace {target}")

    assert result.err == ""
    assert "workspace set to" in result.out
    assert session.workspace == target.resolve()
    assert session.toolsets["file"].workspace == target.resolve()
    assert session.toolsets["terminal"].workspace == target.resolve()
    assert "file, terminal" in result.out


def test_the_system_prompt_follows_the_workspace(
    session: console.ChatSession, tmp_path: Path
) -> None:
    target = tmp_path / "autre-projet"
    target.mkdir()

    commands.dispatch(session, f"/workspace {target}")

    assert str(target.resolve()) in session.history[0]["content"]


def test_memory_is_not_moved(session: console.ChatSession, tmp_path: Path) -> None:
    memory = session.toolsets["memory"]
    before = memory.path

    commands.dispatch(session, f"/workspace {tmp_path}")

    assert memory.path == before, "MEMORY.md belongs to UH, not to a project"


def test_a_missing_directory_is_refused(session: console.ChatSession) -> None:
    before = session.workspace

    result = commands.dispatch(session, "/workspace /nope/does-not-exist")

    assert "no such directory" in result.err
    assert session.workspace == before


def test_a_file_is_not_a_workspace(session: console.ChatSession, tmp_path: Path) -> None:
    target = tmp_path / "fichier.txt"
    target.write_text("x", encoding="utf-8")

    result = commands.dispatch(session, f"/workspace {target}")

    assert "not a directory" in result.err


def test_a_system_directory_is_refused(session: console.ChatSession) -> None:
    before = session.workspace

    result = commands.dispatch(session, f"/workspace {SYSTEM_CWD}")

    assert "system directory" in result.err
    assert session.workspace == before


def test_help_and_completion_know_the_command(session: console.ChatSession) -> None:
    assert commands.resolve("workspace") is not None
    assert "workspace" in commands.completion_candidates("work")

    rendered = commands.dispatch(session, "/help")
    assert "/workspace <path>" in rendered.out

    explained = commands.dispatch(session, "/help workspace")
    assert "writable paths" in explained.out
