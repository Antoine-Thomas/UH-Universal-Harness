"""Lot 1 — les preferences de Skills : persistance atomique et filtrage.

Ce que ces tests protegent : un Skill decoche n'est jamais charge, le choix survit au
redemarrage, un fichier corrompu ne fait pas tomber UH, et rien de tout cela ne peut
elargir une permission (ce module ne parle ni a UHB ni a un outil).
"""

from __future__ import annotations

import json

from uh import skill_prefs

LISTING = [
    {"name": "python", "origin": "native", "title": "Python", "description": "Langage"},
    {"name": "mon-skill", "origin": "user", "title": "Mon skill", "description": "Perso"},
]


def test_a_skill_is_enabled_until_it_is_turned_off(tmp_path) -> None:
    assert skill_prefs.is_enabled("python", "native", home=tmp_path) is True
    assert skill_prefs.disabled(home=tmp_path) == []


def test_turning_a_native_skill_off_never_touches_its_file(tmp_path) -> None:
    skill_prefs.set_enabled("python", "native", False, home=tmp_path)
    assert skill_prefs.is_enabled("python", "native", home=tmp_path) is False
    payload = json.loads((tmp_path / skill_prefs.FILE_NAME).read_text(encoding="utf-8"))
    assert payload == {"disabled": ["native:python"]}


def test_the_choice_survives_a_restart(tmp_path) -> None:
    skill_prefs.set_enabled("mon-skill", "user", False, home=tmp_path)
    # A new process reads the same file: that is what a restart is.
    assert skill_prefs.is_enabled("mon-skill", "user", home=tmp_path) is False
    skill_prefs.set_enabled("mon-skill", "user", True, home=tmp_path)
    assert skill_prefs.is_enabled("mon-skill", "user", home=tmp_path) is True


def test_disabled_skills_are_excluded_from_the_router_view_only(tmp_path) -> None:
    skill_prefs.set_enabled("python", "native", False, home=tmp_path)
    visible = skill_prefs.enabled_only(LISTING, home=tmp_path)
    assert [entry["name"] for entry in visible] == ["mon-skill"]
    # The interface still sees both, with the checkbox off.
    marked = skill_prefs.for_listing(LISTING, home=tmp_path)
    assert {entry["name"]: entry["enabled"] for entry in marked} == {
        "python": False,
        "mon-skill": True,
    }


def test_a_corrupt_file_is_empty_not_fatal(tmp_path) -> None:
    (tmp_path / skill_prefs.FILE_NAME).write_text("{not json", encoding="utf-8")
    assert skill_prefs.disabled(home=tmp_path) == []
    assert skill_prefs.is_enabled("python", "native", home=tmp_path) is True


def test_a_missing_skill_is_never_silently_reenabled(tmp_path) -> None:
    """A preference for a skill that is gone stays written, and does nothing."""
    skill_prefs.set_enabled("disparu", "user", False, home=tmp_path)
    assert "user:disparu" in skill_prefs.disabled(home=tmp_path)
    listing = [{"name": "python", "origin": "native"}]
    assert [entry["name"] for entry in skill_prefs.enabled_only(listing, home=tmp_path)] == ["python"]


def test_ids_are_origin_and_name_never_a_path(tmp_path) -> None:
    assert skill_prefs.skill_id("python", "native") == "native:python"
    assert skill_prefs.skill_id("python", "inherited") == "native:python"
    assert skill_prefs.skill_id("mon-skill", "user") == "user:mon-skill"
    # A traversal attempt is just a name: no directory is ever derived from it.
    assert skill_prefs.skill_id("../../etc/passwd", "user") == "user:../../etc/passwd"
    skill_prefs.set_enabled("../../etc/passwd", "user", False, home=tmp_path)
    assert (tmp_path / skill_prefs.FILE_NAME).is_file()
    assert not (tmp_path.parent / "etc").exists()


def test_an_empty_name_is_refused(tmp_path) -> None:
    try:
        skill_prefs.set_enabled("   ", "user", False, home=tmp_path)
    except skill_prefs.SkillPrefsError as exc:
        assert "needs a name" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("un identifiant vide doit etre refuse")


def test_the_write_leaves_no_temporary_file(tmp_path) -> None:
    skill_prefs.set_enabled("python", "native", False, home=tmp_path)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".skill_prefs")]
    assert leftovers == [], leftovers


def test_setting_the_same_state_twice_is_idempotent(tmp_path) -> None:
    first = skill_prefs.set_enabled("python", "native", False, home=tmp_path)
    second = skill_prefs.set_enabled("python", "native", False, home=tmp_path)
    assert first["disabled"] == second["disabled"] == ["native:python"]


def test_the_router_listing_hides_disabled_skills(
    tmp_path, monkeypatch
) -> None:
    """The seam the turn actually uses, not a copy of it."""
    from uh.console import _skill_listing

    monkeypatch.setenv("UH_HOME", str(tmp_path))
    from uh import skills as uh_skills

    monkeypatch.setattr(
        uh_skills,
        "list_skills",
        lambda *a, **kw: {"skills": [dict(item) for item in LISTING]},
    )
    assert {entry["name"] for entry in _skill_listing()} == {"python", "mon-skill"}
    skill_prefs.set_enabled("python", "native", False, home=tmp_path)
    assert {entry["name"] for entry in _skill_listing()} == {"mon-skill"}
