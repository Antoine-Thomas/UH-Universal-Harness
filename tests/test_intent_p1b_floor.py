"""P1b de G16 : le socle en lecture seule, et les familles qui le remplacent.

Le jeu gelé de ce fichier (`tests/data/intent_holdout_v3.json`, sha256 dans le
message du commit de tests) n'a servi à écrire **aucune** règle : les règles sont
des familles (verbes, objets, extensions), et chaque famille a ici une paraphrase
qu'elle n'a jamais vue. Le tableau complet des trente demandes est produit par
`test_the_frozen_grid`, et le taux de « zéro outil à tort » par
`test_zero_tool_wrong_rate`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from uh import console, intent
from uh.toolsets import build_toolsets

HOLDOUT = Path(__file__).parent / "data" / "intent_holdout_v3.json"
CASES = [
    (case["asked"], bool(case["needs_tool"]), frozenset(case["tools"]))
    for case in json.loads(HOLDOUT.read_text(encoding="utf-8"))["cases"]
]
CATALOG = build_toolsets()


def _offered(asked: str) -> tuple[set[str], set[str]]:
    selection = intent.select(asked, CATALOG)
    offered = intent.turn_toolsets(CATALOG, selection, enabled=True)
    tools = {
        spec["function"]["name"]
        for toolset in offered.values()
        for spec in (getattr(toolset, "tools", None) or [])
    }
    return set(offered), tools


@pytest.mark.parametrize(("asked", "needs_tool", "expected"), CASES)
def test_the_frozen_grid(asked: str, needs_tool: bool, expected: frozenset) -> None:
    _sets, tools = _offered(asked)
    assert tools == set(expected), (asked, sorted(tools), sorted(expected))


def test_zero_tool_wrong_rate() -> None:
    """Le seuil de Thomas : au plus 5 % de demandes utiles laissées sans outil."""
    wrong = [asked for asked, needs, expected in CASES if needs and not _offered(asked)[1]]
    total = sum(1 for _asked, needs, _expected in CASES if needs)
    rate = len(wrong) / total
    assert rate <= 0.05, (round(rate, 3), wrong)


def test_an_unrecognised_request_never_gets_a_write_or_a_terminal() -> None:
    """Le socle est en lecture seule, par décision : jamais d'écriture, jamais de shell."""
    for asked in (
        "quelle strategie pour mon projet demain matin",
        "raconte l'histoire de ce dossier sans le modifier",
        "verifie si ce depot me convient vraiment",
    ):
        _sets, tools = _offered(asked)
        assert tools, f"une demande non reconnue garde de quoi lire : {asked}"
        assert not (tools & {"write_file", "patch", "terminal", "memory_write"}), (asked, tools)


# --------------------------------------------------------------------------- #
# généralité : une paraphrase par famille, jamais vue par les règles
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("asked", "expected"),
    [
        ("est-ce qu'il pleut a Nantes cet apres-midi ?", {"web_search"}),
        ("will it rain in Lille tomorrow?", {"web_search"}),
        ("combien de lignes dans uh/console.py", {"read_file", "search_files"}),
        ("redige un resume dans synthese.md", {"write_file"}),
        ("montre-moi le contenu de docs/API.md", {"read_file"}),
        ("demarre la suite de tests", {"terminal"}),
        ("quels sont les fichiers du repertoire tests", {"search_files"}),
        ("je cherche une image de montagne", {"web_image_search"}),
    ],
)
def test_a_family_rule_survives_a_paraphrase(asked: str, expected: set[str]) -> None:
    _sets, tools = _offered(asked)
    assert tools == expected, (asked, sorted(tools))


# --------------------------------------------------------------------------- #
# horloge : une ligne stable, fixée au démarrage
# --------------------------------------------------------------------------- #
def test_the_prompt_carries_a_stable_session_date(tmp_path: Path) -> None:
    first = console.system_message(tmp_path)["content"]
    second = console.system_message(tmp_path)["content"]
    assert "Session started" in first
    assert console._SESSION_STARTED in first
    assert first == second, "une date qui change à chaque tour casserait le cache"


# --------------------------------------------------------------------------- #
# avis de génération : du texte, et rien qui se lance
# --------------------------------------------------------------------------- #
def test_the_generation_notice_is_text_only(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append("Popen"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: launched.append("run"))
    monkeypatch.setattr(os, "system", lambda *a, **k: launched.append("system"))

    notice = console._generation_notice_for("dessine un chat photo realiste")

    assert notice is not None
    assert "photo" in notice and "installer" in notice
    assert launched == [], "aucune installation, aucune génération lancée"


def test_an_installed_generation_capability_silences_the_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(console, "_generation_available", lambda: True)
    assert console._generation_notice_for("dessine un chat photo realiste") is None


def test_a_photo_request_is_not_a_generation_request() -> None:
    assert console._generation_notice_for("trouve une photo de chat") is None
