"""P1c de G16 : une relance hérite de l'offre du tour précédent.

Le jeu gelé de P1b ne contient que des phrases complètes. Or « oui », « vas-y »,
« et demain ? », « pareil pour Paris », « refais-le » suivent une proposition de
l'assistant et demandent la même capacité : les classer « conversation » coupe
l'agent au premier « oui ».

Le jeu de relances (`tests/data/intent_holdout_followups.json`, sha256 dans le
message du commit de tests) a été écrit avant la règle. Ce que ces tests fixent :

* une relance hérite de l'offre du tour précédent de l'utilisateur, jamais plus ;
* sans tour précédent, il n'y a rien à poursuivre : zéro outil ;
* une relance après une conversation reçoit le socle lecture seule ;
* une relance ne contourne jamais le mode (permissions) ;
* dans un même tour, la boucle d'outils garde la même offre à chaque itération.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uh import agent, intent, mode_gate
from uh.toolsets import build_toolsets

HOLDOUT = Path(__file__).parent / "data" / "intent_holdout_followups.json"
CASES = json.loads(HOLDOUT.read_text(encoding="utf-8"))["cases"]
CATALOG = build_toolsets()


def _offered(asked: str, history: list[str], catalog: dict | None = None) -> set[str]:
    catalog = catalog or CATALOG
    messages = [{"role": "user", "content": text} for text in history]
    selection = intent.select(asked, catalog, history=messages)
    offered = intent.turn_toolsets(catalog, selection, enabled=True)
    return {
        spec["function"]["name"]
        for toolset in offered.values()
        for spec in (getattr(toolset, "tools", None) or [])
    }


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[
        f"{case['asked']} <- {case['history'][-1] if case['history'] else 'session'}"
        for case in CASES
    ],
)
def test_a_follow_up_inherits_the_previous_offer(case: dict) -> None:
    offered = _offered(case["asked"], case["history"])
    assert offered == set(case["tools"]), (case["asked"], sorted(offered), case["tools"])


def test_no_follow_up_ever_widens_to_a_write_or_a_terminal() -> None:
    """Une relance hérite, elle n'invente pas : rien de plus que le tour d'avant."""
    for case in CASES:
        offered = _offered(case["asked"], case["history"])
        inherited = set(case["tools"])
        assert offered <= inherited, (case["asked"], sorted(offered), sorted(inherited))


def test_a_follow_up_after_a_mode_refusal_does_not_bypass_the_mode() -> None:
    """Le mode passe après le routeur : hériter ne rend aucune permission."""
    messages = [{"role": "user", "content": "lance les tests"}]
    selection = intent.select("continue", CATALOG, history=messages)
    offered = intent.turn_toolsets(CATALOG, selection, enabled=True)
    assert "terminal" in {
        spec["function"]["name"]
        for toolset in offered.values()
        for spec in (getattr(toolset, "tools", None) or [])
    }

    view, withheld = mode_gate.read_only_view(offered, "chat")

    assert "terminal" in withheld
    assert "terminal" not in {
        spec["function"]["name"]
        for toolset in view.values()
        for spec in (getattr(toolset, "tools", None) or [])
    }


class _TwoCalls:
    """Un fournisseur qui demande deux outils, puis répond : l'offre ne bouge pas."""

    def __init__(self) -> None:
        self.offered: list[list[str]] = []
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.offered.append(sorted(spec["function"]["name"] for spec in tools or []))
        self.calls += 1
        if self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "a",
                        "type": "function",
                        "function": {"name": "search_files", "arguments": "{}"},
                    }
                ],
            }
        return {"role": "assistant", "content": "fini", "tool_calls": []}


def test_the_tool_loop_keeps_the_same_offer_on_every_iteration() -> None:
    offered_toolsets = intent.turn_toolsets(
        CATALOG, intent.select("liste les fichiers du dossier docs", CATALOG), enabled=True
    )
    provider = _TwoCalls()

    agent.run_turn([{"role": "user", "content": "liste les fichiers"}], provider, offered_toolsets)

    assert len(provider.offered) == 2
    assert provider.offered[0] == provider.offered[1], (
        "l'offre ne doit pas changer en cours de tour"
    )
    assert provider.offered[0] == ["search_files"]
