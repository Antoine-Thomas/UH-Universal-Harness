"""Memory toolset: flat markdown file, section replacement (no network)."""

from __future__ import annotations

from pathlib import Path

from uh.toolsets.memory import MemoryToolset


def test_first_write_creates_file_with_all_sections(tmp_path: Path) -> None:
    toolset = MemoryToolset(home=tmp_path / "uh")
    result = toolset.call("memory_write", {"section": "Notes", "content": "- first note"})

    assert "error" not in result, result
    assert result["created"] is True
    assert toolset.path.exists()

    text = toolset.path.read_text(encoding="utf-8")
    assert "## Notes" in text
    assert "## Preferences" in text
    assert "## Learned" in text
    assert "- first note" in text


def test_other_sections_are_preserved(tmp_path: Path) -> None:
    toolset = MemoryToolset(home=tmp_path / "uh")
    toolset.call("memory_write", {"section": "Notes", "content": "- note one"})
    toolset.call("memory_write", {"section": "Preferences", "content": "- answers in French"})
    toolset.call("memory_write", {"section": "Notes", "content": "- note two"})

    sections = toolset.call("memory_read", {})["sections"]

    assert sections["Notes"].strip() == "- note two"
    assert sections["Preferences"].strip() == "- answers in French"
    assert "note one" not in sections["Notes"]


def test_unknown_section_is_refused(tmp_path: Path) -> None:
    toolset = MemoryToolset(home=tmp_path / "uh")
    result = toolset.call("memory_write", {"section": "Secrets", "content": "nope"})
    assert "error" in result
    assert not toolset.path.exists()


def test_read_before_any_write_is_empty_not_an_error(tmp_path: Path) -> None:
    toolset = MemoryToolset(home=tmp_path / "uh")
    result = toolset.call("memory_read", {})
    assert "error" not in result
    assert result["exists"] is False
    assert result["sections"] == {"Notes": "", "Preferences": "", "Learned": ""}
