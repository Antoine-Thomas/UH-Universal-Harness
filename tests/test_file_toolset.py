"""File toolset: sandbox and basic read/write/patch behaviour (no network)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh.toolsets.file import FileToolset


def test_write_then_read(tmp_path: Path) -> None:
    toolset = FileToolset(workspace=tmp_path)

    written = toolset.call("write_file", {"path": "notes/a.txt", "content": "hello\n"})
    assert "error" not in written, written
    assert written["created"] is True
    assert (tmp_path / "notes" / "a.txt").read_text(encoding="utf-8") == "hello\n"

    read = toolset.call("read_file", {"path": "notes/a.txt"})
    assert "error" not in read, read
    assert read["content"] == "hello\n"
    assert read["path"] == "notes/a.txt"


def test_read_missing_file_is_an_error(tmp_path: Path) -> None:
    toolset = FileToolset(workspace=tmp_path)
    result = toolset.call("read_file", {"path": "nope.txt"})
    assert "error" in result


def test_patch_requires_a_unique_anchor(tmp_path: Path) -> None:
    toolset = FileToolset(workspace=tmp_path)
    toolset.call("write_file", {"path": "code.py", "content": "a = 1\nb = 2\n"})

    ambiguous = toolset.call("patch", {"path": "code.py", "old_string": "= ", "new_string": "= 9"})
    assert "error" in ambiguous
    assert (tmp_path / "code.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"

    ok = toolset.call("patch", {"path": "code.py", "old_string": "b = 2", "new_string": "b = 3"})
    assert "error" not in ok, ok
    assert (tmp_path / "code.py").read_text(encoding="utf-8") == "a = 1\nb = 3\n"


def test_reject_parent_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    toolset = FileToolset(workspace=workspace)

    result = toolset.call("write_file", {"path": "../outside.txt", "content": "x"})

    assert "error" in result
    assert "outside the workspace" in result["error"]
    assert not (tmp_path / "outside.txt").exists()


def test_reject_absolute_path_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    toolset = FileToolset(workspace=workspace)

    result = toolset.call("read_file", {"path": "/etc/passwd"})

    assert "error" in result
    assert "not found" not in result["error"]  # refused by the sandbox, not a 404


def test_reject_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    link = workspace / "link.txt"

    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available for this user on this platform")

    toolset = FileToolset(workspace=workspace)
    result = toolset.call("read_file", {"path": "link.txt"})

    assert "error" in result
    assert "top secret" not in str(result)


def test_workspace_defaults_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_WORKSPACE", str(tmp_path))
    toolset = FileToolset()
    assert Path(os.path.realpath(toolset.workspace)) == Path(os.path.realpath(tmp_path))


def test_search_files_finds_pattern(tmp_path: Path) -> None:
    toolset = FileToolset(workspace=tmp_path)
    toolset.call("write_file", {"path": "pkg/a.py", "content": "def target():\n    pass\n"})
    toolset.call("write_file", {"path": "pkg/b.txt", "content": "target\n"})

    result = toolset.call("search_files", {"pattern": r"def target", "file_glob": "*.py"})

    assert "error" not in result, result
    assert result["count"] == 1
    assert result["matches"][0]["path"] == "pkg/a.py"
