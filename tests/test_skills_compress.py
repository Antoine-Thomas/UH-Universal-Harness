"""Merging skills that say the same thing - and keeping the originals."""

from __future__ import annotations

from pathlib import Path

from uh import skills

TEMPLATE = """# {title}

> {description}

## Instructions

{body}
"""


def _write_skill(home: Path, name: str, title: str, description: str, body: str) -> Path:
    folder = skills.user_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.md"
    path.write_text(TEMPLATE.format(title=title, description=description, body=body), encoding="utf-8")
    return path


def test_two_skills_with_the_same_title_are_a_group(tmp_path: Path) -> None:
    _write_skill(tmp_path, "python-1", "Écrire du Python", "Bonnes pratiques Python", "A" * 40)
    _write_skill(tmp_path, "python-2", "Écrire du Python", "Bonnes pratiques Python", "B" * 80)

    groups = skills.duplicate_groups(home=tmp_path)

    assert len(groups) == 1
    assert {entry["name"] for entry in groups[0]} == {"python-1", "python-2"}


def test_different_skills_are_not_a_group(tmp_path: Path) -> None:
    _write_skill(tmp_path, "rust", "Rust et les emprunts", "Le borrow checker en pratique", "x" * 50)
    _write_skill(tmp_path, "php", "PHP moderne", "Typage et Composer", "y" * 50)

    assert skills.duplicate_groups(home=tmp_path) == []


def test_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    first = _write_skill(tmp_path, "python-1", "Écrire du Python", "Bonnes pratiques", "court")
    _write_skill(tmp_path, "python-2", "Écrire du Python", "Bonnes pratiques", "beaucoup plus long")
    before = first.read_text(encoding="utf-8")

    report = skills.compress(skills.duplicate_groups(home=tmp_path), home=tmp_path, write=False)

    assert report and report[0]["applied"] is False
    assert first.read_text(encoding="utf-8") == before


def test_a_merge_keeps_the_absorbed_files(tmp_path: Path) -> None:
    """A bad merge must be one move away from being undone."""
    _write_skill(tmp_path, "python-1", "Écrire du Python", "Bonnes pratiques", "court")
    _write_skill(tmp_path, "python-2", "Écrire du Python", "Bonnes pratiques", "b" * 90)

    skills.compress(skills.duplicate_groups(home=tmp_path), home=tmp_path, write=True)

    keeper = skills.user_dir(tmp_path) / "python-2.md"
    stashed = skills.user_dir(tmp_path) / ".merged" / "python-1.md"
    assert keeper.is_file()
    assert "Merged from python-1" in keeper.read_text(encoding="utf-8")
    assert stashed.is_file(), "nothing is deleted: the source is parked, not eaten"
    assert not (skills.user_dir(tmp_path) / "python-1.md").exists()
