"""P1 de G16 : le routeur rétrécit vraiment, et un refus dit comment revenir.

Mesure de départ (26/11, catalogue réel, 7 demandes) : cinq d'entre elles
recevaient **les quatre toolsets et les huit outils**. La docstring de
`uh/intent.py` promettait déjà « rien au-dessus du seuil → aucune capacité » ;
la promesse n'était pas codée (`turn_toolsets` rendait le catalogue entier).

Ce que ces tests fixent, dans l'ordre :

* la règle 4, pour de vrai : sous le seuil → **zéro** outil ;
* le vocabulaire image : une photo désigne `web_search` (qui publie
  `web_image_search`), sans réveiller `terminal` ni `patch` ;
* une demande de fabrication (« dessine… ») n'obtient pas la photothèque ;
* un outil écarté se nomme « non offert ce tour » et dit comment le redemander ;
* « avec tous les outils » est la porte de sortie explicite de l'opérateur ;
* aucun outil n'est jamais exécuté par le routeur (il propose, UHB décide).
"""

from __future__ import annotations

import pytest

from uh import agent, intent
from uh.toolsets import build_toolsets


def _catalog() -> dict:
    return build_toolsets()


def _names(toolsets: dict) -> set[str]:
    return {
        spec["function"]["name"]
        for toolset in toolsets.values()
        for spec in (getattr(toolset, "tools", None) or [])
    }


def _offered(asked: str, catalog: dict | None = None) -> tuple[set[str], set[str]]:
    catalog = catalog or _catalog()
    selection = intent.select(asked, catalog)
    offered = intent.turn_toolsets(catalog, selection, enabled=True)
    return set(offered), _names(offered)


# --------------------------------------------------------------------------- #
# la règle 4, mesurée sur les sept demandes de l'opérateur
# --------------------------------------------------------------------------- #
CASES = [
    # (demande, toolsets attendus, outils attendus)
    ("bonjour", set(), set()),
    ("je voudrais la photo d'un chat", {"web_search"}, {"web_search", "web_image_search"}),
    ("montre-moi une vraie photo d'un chat", {"web_search"}, {"web_search", "web_image_search"}),
    # Le toolset `web_search` publie les deux recherches : offrir est par toolset,
    # couper un schéma demanderait de remplacer l'objet — ce que l'architecture
    # interdit (test_t7). Trois outils, contre huit auparavant.
    (
        "actualites python",
        {"web_search", "terminal"},
        {"web_search", "web_image_search", "terminal"},
    ),
    # Aucune capacité ne rend l'heure : le modèle répond, il n'appelle rien.
    ("quelle heure est-il", set(), set()),
    # Fabriquer une image, c'est G13 : la photothèque n'est pas la réponse.
    ("dessine un chat photo realiste", set(), set()),
    # Corriger du code demande de lire, écrire, patcher et chercher : 4 outils.
    ("corrige le bug dans app.py", {"file"}, {"read_file", "write_file", "patch", "search_files"}),
]


@pytest.mark.parametrize(("asked", "toolsets", "tools"), CASES)
def test_the_seven_real_requests_get_what_they_need(
    asked: str, toolsets: set[str], tools: set[str]
) -> None:
    offered_sets, offered_tools = _offered(asked)
    assert offered_sets == toolsets, (asked, offered_sets)
    assert offered_tools == tools, (asked, offered_tools)


def test_no_request_ever_gets_the_whole_catalogue_any_more() -> None:
    """La régression d'origine : tout offrir par défaut, sans le dire."""
    catalog = _catalog()
    everything = _names(catalog)
    for asked, _sets, _tools in CASES:
        selection = intent.select(asked, catalog)
        offered = intent.turn_toolsets(catalog, selection, enabled=True)
        assert _names(offered) != everything, (asked, sorted(_names(offered)))


def test_below_the_threshold_means_no_tool() -> None:
    """La règle 4, sans passer par une phrase : une sélection vide vaut zéro outil."""
    catalog = _catalog()
    empty = intent.Selection(confidence="medium", reason="aucune capacite au-dessus du seuil")
    assert intent.turn_toolsets(catalog, empty, enabled=True) == {}


# --------------------------------------------------------------------------- #
# la porte de sortie explicite
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "asked",
    ["je voudrais la photo d'un chat, avec tous les outils", "all tools: corrige app.py"],
)
def test_the_operator_can_ask_for_every_tool(asked: str) -> None:
    catalog = _catalog()
    selection = intent.select(asked, catalog)
    offered = intent.turn_toolsets(catalog, selection, enabled=True)
    assert set(offered) == set(catalog)


# --------------------------------------------------------------------------- #
# un refus dit pourquoi, et comment revenir
# --------------------------------------------------------------------------- #
def test_a_withheld_tool_is_named_with_its_way_back() -> None:
    catalog = _catalog()
    selection = intent.select("je voudrais la photo d'un chat", catalog)
    intent.reset_refusals()

    answer = intent.explain_withheld("terminal", catalog, selection)

    assert answer is not None
    assert answer["reason"] == "not_offered"
    assert answer["tool"] == "terminal"
    assert answer["type"] == "forbidden"
    assert "tous les outils" in answer["error"]
    assert answer["retry"] == "avec tous les outils"
    assert intent.refusal_count("terminal") == 1


def test_an_offered_tool_is_not_refused_by_the_router() -> None:
    catalog = _catalog()
    selection = intent.select("je voudrais la photo d'un chat", catalog)
    assert intent.explain_withheld("web_image_search", catalog, selection) is None


def test_in_an_empty_selection_every_tool_is_withheld() -> None:
    catalog = _catalog()
    selection = intent.select("quelle heure est-il", catalog)
    answer = intent.explain_withheld("read_file", catalog, selection)
    assert answer is not None and answer["reason"] == "not_offered"


# --------------------------------------------------------------------------- #
# le câblage : le refus traverse `agent._execute`
# --------------------------------------------------------------------------- #
def test_the_agent_ask_the_caller_before_saying_unknown_tool() -> None:
    seen: list[str] = []

    def explain(name: str) -> dict:
        seen.append(name)
        return {
            "error": f"`{name}` n'est pas offert ce tour",
            "type": "forbidden",
            "tool": name,
            "reason": "not_offered",
            "retry": "avec tous les outils",
        }

    message, refused = agent._execute({}, "terminal", {}, explain_missing=explain)

    assert refused is True
    assert seen == ["terminal"]
    assert '"reason": "not_offered"' in message["content"]
    assert "tous les outils" in message["content"]


def test_the_router_never_runs_anything() -> None:
    """Proposer n'est pas exécuter : le catalogue reste intact après une sélection."""
    catalog = _catalog()
    before = {name: id(toolset) for name, toolset in catalog.items()}
    intent.turn_toolsets(
        catalog, intent.select("je voudrais la photo d'un chat", catalog), enabled=True
    )
    assert {name: id(toolset) for name, toolset in catalog.items()} == before


def test_the_mode_wins_over_the_router_explainer() -> None:
    """Une porte fermée par le mode reste fermée par le mode.

    L'explicateur du routeur ne doit jamais transformer un refus de permission en
    « pas offert ce tour » : l'interface affiche « Autoriser pour cette
    conversation » sur `mode_forbidden`, et rien sur `not_offered`. L'ordre des
    causes est donc une garantie, pas un détail de rédaction.
    """

    class Gate:
        def explain_missing(self, name: str) -> dict:
            return {
                "error": f"`{name}` n'est pas proposé dans un mode en lecture seule",
                "type": "forbidden",
                "tool": name,
                "reason": "mode_forbidden",
                "mode": "chat",
            }

    def router(name: str) -> dict:
        return {
            "error": f"`{name}` n'est pas offert pour ce tour",
            "type": "forbidden",
            "tool": name,
            "reason": "not_offered",
            "retry": "avec tous les outils",
        }

    message, refused = agent._execute(
        {}, "write_file", {}, toolsets={"file": Gate()}, explain_missing=router
    )

    assert refused is True
    assert "mode_forbidden" in message["content"]
    assert "not_offered" not in message["content"], "le mode doit rester la cause"
