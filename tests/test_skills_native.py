"""The shipped coding skills: present, parsed, and actually expert-sized.

The panel lists them with a "Natif" badge, and the chat can attach one by name, so
the two things worth asserting are that they ship with the package and that each
file parses into a title, a description and instructions with a review checklist -
a skill that renders as "Untitled skill" with an empty body is worse than none.
"""

from __future__ import annotations

import pytest

from uh import skills

EXPECTED = {
    "angular",
    "cpp-opengl",
    "html-css",
    "java",
    "javascript",
    "pascal",
    "php",
    "python",
    "react",
    "ruby",
    "rust",
    "typescript",
}


def native_files() -> dict:
    listing = skills.list_skills()
    return {entry["name"]: entry for entry in listing["skills"] if entry["origin"] == "native"}


def test_the_native_root_is_not_the_module_name() -> None:
    """``uh/skills.py`` is a module: a ``uh/skills/`` folder would shadow it."""
    assert skills.NATIVE_ROOT.name == "skills_native"
    assert skills.NATIVE_ROOT.is_dir()


def test_every_language_the_cockpit_advertises_is_shipped() -> None:
    assert set(native_files()) == EXPECTED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_skill_parses_into_something_usable(name: str) -> None:
    entry = skills.read_skill(name)
    assert entry is not None, f"{name} must be readable through the public API"
    assert entry["origin"] == "native"
    assert entry["title"] and entry["title"] != "Untitled skill"
    assert len(entry["description"]) >= 40, "the description is what the picker shows"
    instructions = entry["instructions"]
    assert len(instructions) >= 800, f"{name} is a paragraph, not a skill"
    assert "Review checklist" in instructions, f"{name} must end with something to check"


def test_the_skills_are_shipped_in_the_wheel() -> None:
    """Package data, asserted where it can be: the glob is declared in pyproject."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'skills_native/*.md' in pyproject


def test_a_coding_skill_can_be_attached_to_a_turn() -> None:
    block = skills.prompt_block("python")
    assert block and "Python" in block
    assert "Review checklist" in block
