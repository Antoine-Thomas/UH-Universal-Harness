"""Lot 4 — capacites declaratives : parsing, nettoyage des instructions, routage.

Un Skill oriente le choix d'un outil ; il n'autorise jamais son execution.
"""

from __future__ import annotations

import pytest

from uh import skills
from uh.intent import select, turn_toolsets

L = chr(10)
FULL = (
    "# Graphiques"
    + L
    + L
    + "> Generer des graphiques."
    + L
    + L
    + "## Capacites"
    + L
    + "- file"
    + L
    + "- uhb"
    + L
    + L
    + "## Instructions"
    + L
    + L
    + "Analyse les donnees puis produis un PNG."
    + L
    + L
    + "## Notes"
    + L
    + "Garde cette section."
)


def entry(**kwargs):
    base = {"name": "graph", "title": "Graphiques", "capabilities": ["file", "uhb"]}
    base.update(kwargs)
    return base


# 1. Capacite valide reconnue
def test_a_declared_capability_is_read() -> None:
    assert skills.parse_capabilities(FULL) == ["file", "uhb"]


# 2. Capacite inconnue : lue, jamais executee, et signalee si elle n'existe pas
def test_an_unknown_capability_is_kept_verbatim_and_ignored_by_routing() -> None:
    text = "## Capacites" + L + "- fantome" + L + "- file"
    assert skills.parse_capabilities(text) == ["fantome", "file"]
    selection = select(
        "fais un graphique de ces donnees",
        catalog={"file": object()},
        skills=[entry(capabilities=["fantome"])],
    )
    assert "fantome" not in selection.toolsets
    assert "ignorees" in selection.reason


# 3. Skill mal formee : aucune capacite, aucun crash
@pytest.mark.parametrize(
    "text",
    ["", "pas de titre", "## Capacites", "## Capacites" + L + "-", "## Capacités" + L + "-   "],
)
def test_a_malformed_skill_yields_no_capability(text: str) -> None:
    assert skills.parse_capabilities(text) == []
    assert skills.parse(text)["instructions"] is not None


# 4. Les instructions ne contiennent plus la section
def test_instructions_do_not_contain_the_capability_section() -> None:
    parsed = skills.parse(FULL)
    body = parsed["instructions"]
    assert "Capacit" not in body and "capacit" not in body
    assert "- file" not in body and "- uhb" not in body
    assert "Analyse les donnees puis produis un PNG." in body


# 5. Les autres sections sont preservees
def test_other_sections_are_preserved() -> None:
    body = skills.parse(FULL)["instructions"]
    assert "## Notes" in body and "Garde cette section." in body


# 5bis. Une Skill historique sans section garde ses instructions telles quelles
def test_a_historical_skill_is_untouched() -> None:
    assert (
        skills.parse("# P" + L + L + "## Instructions" + L + "Fais X.")["instructions"] == "Fais X."
    )


# 6. Skill desactivee : aucune contribution au routage
def test_a_disabled_skill_does_not_contribute() -> None:
    """Un Skill desactive n'est pas transmis au routeur : il ne peut rien proposer.

    C'est la liste passee a select() qui porte l'activation (Lot 1) : absente, la
    skill n'existe pas pour le routage et ses capacites ne pesent pas.
    """
    disabled = select("fais un graphique de ces donnees", catalog={"file": object()}, skills=[])
    assert disabled.skill is None
    enabled = select(
        "fais un graphique de ces donnees", catalog={"file": object()}, skills=[entry()]
    )
    assert enabled.skill is not None
    assert "file" in enabled.toolsets


# 7. Selection contextuelle correcte : la demande pertinente selectionne le toolset
def test_a_relevant_request_selects_the_right_toolset() -> None:
    selection = select(
        "fais un graphique de ces donnees",
        catalog={"file": object(), "web_search": object()},
        skills=[entry()],
    )
    assert "file" in selection.toolsets


# 8. Conversation ordinaire : aucune selection artificielle
@pytest.mark.parametrize("prompt", ["bonjour", "comment vas-tu ?", "merci beaucoup"])
def test_ordinary_conversation_selects_nothing(prompt: str) -> None:
    assert select(prompt, catalog={"file": object()}, skills=[entry()]).toolsets == ()


# 9. Intersection stricte : aucun toolset fantome ne sort du routeur
def test_no_ghost_toolset_survives_the_intersection() -> None:
    selection = select(
        "fais un graphique avec uhb",
        catalog={"file": object()},
        skills=[entry(capabilities=["uhb", "positron"])],
    )
    assert "uhb" not in selection.toolsets
    available = turn_toolsets({"file": object()}, selection, enabled=True)
    assert set(available) <= {"file"}
