"""Skills: markdown files the operator owns, read and written without a database.

The parser is forgiving because a skill is written by hand; the writer is strict
because a name becomes a path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import skills

TEMPLATE_OUTPUT = """# Ma procedure

> une ligne de description

## Instructions

Etape 1 : ouvrir le fichier.
Etape 2 : le lire.
"""


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def test_the_template_round_trips() -> None:
    parsed = skills.parse(TEMPLATE_OUTPUT)

    assert parsed["title"] == "Ma procedure"
    assert parsed["description"] == "une ligne de description"
    assert parsed["instructions"].startswith("Etape 1")
    assert "Etape 2" in parsed["instructions"]


def test_a_hand_written_file_without_headings_is_still_a_skill() -> None:
    parsed = skills.parse("Fais toujours ceci, puis cela.\nJamais autrement.\n")

    assert parsed["title"] == "Untitled skill"
    assert parsed["description"] == ""
    assert "Jamais autrement" in parsed["instructions"]


def test_the_first_line_that_is_not_a_title_is_kept() -> None:
    """A file with no ``#`` heading must not lose its first line."""
    parsed = skills.parse("# Titre\n\nPremiere consigne.\n")

    assert parsed["title"] == "Titre"
    assert parsed["instructions"].startswith("Premiere consigne.")


def test_a_description_is_taken_once_and_only_near_the_top() -> None:
    parsed = skills.parse("# T\n\n> la bonne\n\ncorps\n\n> pas celle-la\n")

    assert parsed["description"] == "la bonne"
    assert "pas celle-la" in parsed["instructions"]


def test_a_missing_title_falls_back_to_the_file_name() -> None:
    parsed = skills.parse("des instructions\n", fallback_name="mon-skill")

    assert parsed["title"] == "mon-skill"


def test_the_slug_folds_accents_and_spaces(home: Path) -> None:
    created = skills.create(
        title="Ma procédure de revue", description="d", instructions="i", home=home
    )

    assert created["name"] == "ma-procedure-de-revue"
    assert (home / "skills" / "ma-procedure-de-revue.md").is_file()


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def test_a_new_skill_is_written_as_markdown(home: Path) -> None:
    created = skills.create(
        title="Revue de code",
        description="relire avant de committer",
        instructions="Lis le diff. Cherche les erreurs.",
        home=home,
    )

    text = Path(created["path"]).read_text(encoding="utf-8")
    assert text.startswith("# Revue de code")
    assert "> relire avant de committer" in text
    assert "## Instructions" in text
    assert "Cherche les erreurs." in text


def test_a_title_alone_is_refused(home: Path) -> None:
    with pytest.raises(skills.SkillError, match="instructions"):
        skills.create(title="Vide", description="", instructions="", home=home)


def test_no_title_is_refused(home: Path) -> None:
    with pytest.raises(skills.SkillError, match="title"):
        skills.create(title="   ", description="", instructions="des instructions", home=home)


def test_an_existing_name_is_refused(home: Path) -> None:
    skills.create(title="Doublon", description="", instructions="i", home=home)

    with pytest.raises(skills.SkillError, match="already exists"):
        skills.create(title="Doublon", description="", instructions="j", home=home)


@pytest.mark.parametrize("name", ["../evade", "avec/espace", "avec espace", "a" * 80, ".cache", "majuscule!"])
def test_a_name_that_could_escape_the_folder_is_refused(home: Path, name: str) -> None:
    with pytest.raises(skills.SkillError, match="file name"):
        skills.create(title="T", description="", instructions="i", name=name, home=home)


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #
def test_listing_separates_user_and_native(home: Path, tmp_path: Path, monkeypatch) -> None:
    (home / "skills").mkdir()
    (home / "skills" / "moi.md").write_text("# A moi\n\n> mien\n\ncorps\n", encoding="utf-8")
    native = tmp_path / "native"
    native.mkdir()
    (native / "livre.md").write_text("# Livre\n\n> natif\n\ncorps\n", encoding="utf-8")
    monkeypatch.setattr(skills, "NATIVE_ROOT", native)

    found = skills.list_skills(home)

    assert found["user"] == 1
    assert found["native"] == 1
    assert found["native_available"] is True
    origins = {entry["name"]: entry["origin"] for entry in found["skills"]}
    assert origins == {"moi": "user", "livre": "native"}
    assert [entry["native"] for entry in found["skills"]] == [False, True]


def test_an_absent_folder_is_an_empty_list(home: Path, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(skills, "NATIVE_ROOT", tmp_path / "nope")

    found = skills.list_skills(home)

    assert found["skills"] == []
    assert found["native_available"] is False


def test_reading_one_skill_returns_its_text(home: Path) -> None:
    skills.create(title="Lisible", description="d", instructions="le corps", home=home)

    found = skills.read_skill("lisible", home=home)

    assert found is not None
    assert found["origin"] == "user"
    assert "le corps" in found["text"]


def test_reading_an_unknown_or_unsafe_name_returns_nothing(home: Path) -> None:
    assert skills.read_skill("absent", home=home) is None
    assert skills.read_skill("../config", home=home) is None
