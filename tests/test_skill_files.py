"""Lot 3 (socle) — modifier, dupliquer, supprimer un Skill utilisateur.

Aucun Skill natif n'est atteignable : `update`, `delete` et `duplicate` n'opèrent que
dans `user_dir()`, et le nom est validé contre `NAME` avant qu'un chemin ne soit
construit. Ce n'est donc pas une question de permission mais d'adressabilité.
"""

from __future__ import annotations

import pytest

from uh import skills


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    (tmp_path / "skills").mkdir()
    return tmp_path


def make(name: str = "mon-skill") -> str:
    skills.create(name=name, title="Mon skill", description="d", instructions="Fais ceci.")
    return name


def test_update_rewrites_the_file(home) -> None:
    name = make()
    result = skills.update(
        name=name,
        title="Nouveau titre",
        description="autre",
        instructions="Nouvelles instructions.",
    )
    assert result["title"] == "Nouveau titre"
    again = skills.read_skill(name)
    assert "Nouvelles instructions." in str(again["instructions"])
    assert "Fais ceci." not in str(again["instructions"])


def test_update_refuses_a_skill_that_is_not_a_user_skill(home) -> None:
    with pytest.raises(skills.SkillError, match="not a user skill"):
        skills.update(name="python", title="x", description="", instructions="y")


def test_update_requires_a_title_and_instructions(home) -> None:
    name = make()
    with pytest.raises(skills.SkillError, match="needs a title"):
        skills.update(name=name, title="  ", description="", instructions="quelque chose")
    with pytest.raises(skills.SkillError, match="needs instructions"):
        skills.update(name=name, title="Titre", description="", instructions="   ")


def test_update_rejects_an_invalid_name_before_touching_any_path(home) -> None:
    with pytest.raises(skills.SkillError, match="file name"):
        skills.update(name="../evil", title="t", description="", instructions="i")


def test_duplicate_creates_an_independent_copy(home) -> None:
    name = make()
    copy = skills.duplicate(name)
    assert copy["name"] == "mon-skill-copie"
    skills.update(name=copy["name"], title="Titre copie", description="d2", instructions="Autre.")
    original = skills.read_skill(name)
    assert str(original["title"]) == "Mon skill", "la copie ne touche pas l'original"


def test_duplicate_refuses_a_native_skill(home) -> None:
    with pytest.raises(skills.SkillError, match="not a user skill"):
        skills.duplicate("python")


def test_delete_removes_a_user_skill(home) -> None:
    name = make()
    assert (home / "skills" / f"{name}.md").is_file()
    assert skills.delete(name)["deleted"] is True
    assert not (home / "skills" / f"{name}.md").exists()


def test_delete_refuses_a_native_skill(home) -> None:
    with pytest.raises(skills.SkillError, match="not a user skill"):
        skills.delete("python")


def test_an_imported_file_keeps_the_uh_format(home) -> None:
    text = "# Skill importe\n\n> Une description.\n\n## Instructions\n\nFais ceci.\n"
    parsed = skills.parse(text)
    created = skills.create(
        name="importe",
        title=parsed["title"],
        description=parsed["description"],
        instructions=parsed["instructions"],
    )
    raw = (home / "skills" / f"{created['name']}.md").read_bytes()
    assert b"\r\n" not in raw, "le fichier ecrit doit etre en LF"
    assert raw.startswith(b"# Skill importe")
