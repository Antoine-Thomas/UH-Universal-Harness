"""Chantier orchestration : la selection des capacites, prouvee sans modele.

Le contrat teste ici est celui du cahier des charges : une demande en langage
naturel doit atteindre le modele avec les capacites pertinentes, sans commande
`/...`, sans tous les outils, sans outil fantome, et sans appel artificiel quand
rien n'est necessaire.
"""

from __future__ import annotations

from uh import intent

# Un catalogue au meme format que `build_toolsets()` (specs OpenAI-compatibles).
CATALOG = {
    "file": [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file from the workspace",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List the files of a directory",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        },
    ],
    "terminal": [
        {
            "type": "function",
            "function": {
                "name": "run_command",
                "description": "Run a shell command",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            },
        }
    ],
    "web_search": [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search the web and return results",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ],
    "uhb": [
        {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "Execute a Python snippet, write files, draw a chart with matplotlib",
                "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
            },
        }
    ],
    "memory": [
        {
            "type": "function",
            "function": {
                "name": "memory_search",
                "description": "Search long-term memory notes",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
        }
    ],
    "mcp:atelier": [
        {
            "type": "function",
            "function": {
                "name": "mcp__atelier__directory_tree",
                "description": "Tree of a directory through the MCP atelier server",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ],
}

SKILLS = [
    {
        "name": "data-science-visualization",
        "title": "Analyse de donnees et graphiques scientifiques",
        "description": "Chaine l'analyse statistique et un graphique matplotlib lisible, produit dans execute_code",
    },
    {
        "name": "scientific-literature-rag",
        "title": "Recherche documentaire : base locale d'abord",
        "description": "Interroger le RAG local et les notes avant toute recherche web",
    },
]


def names(text: str, **kwargs) -> tuple[str, ...]:
    return intent.select(text, CATALOG, skills=SKILLS, **kwargs).toolsets


def test_t1_a_chart_request_needs_no_command() -> None:
    selected = names("Fais-moi un graphique de ces données.")
    assert "uhb" in selected, selected
    assert "web_search" not in selected, selected


def test_t2_a_search_request_needs_no_web_prefix() -> None:
    selected = names("Recherche les dernières informations sur ce sujet.")
    assert "web_search" in selected, selected


def test_t3_an_ambiguous_short_request_asks_instead_of_choosing() -> None:
    selection = intent.select("Peux-tu regarder ça ?", CATALOG, skills=SKILLS)
    if selection.ask:
        assert selection.ask.endswith("?")
        assert selection.toolsets == ()
    else:  # a single clear family is also acceptable — but never a random mix
        assert len(selection.toolsets) <= 1, selection


def test_t4_two_capabilities_are_both_kept() -> None:
    selected = names("Analyse ce fichier CSV et fais un graphique de l'évolution.")
    assert "file" in selected and "uhb" in selected, selected


def test_t5_the_matching_skill_is_proposed() -> None:
    selection = intent.select(
        "Trace une courbe et calcule la moyenne de mes chiffres", CATALOG, skills=SKILLS
    )
    assert selection.skill == "data-science-visualization", selection.skill


def test_t6_an_empty_catalog_never_crashes_and_selects_nothing() -> None:
    selection = intent.select("fais un graphique", {}, skills=())
    assert selection.toolsets == () and selection.skill is None
    assert intent.filter_toolsets({}, selection) == {}


def test_t12_a_conversational_message_gets_no_tool_at_all() -> None:
    selection = intent.select("Bonjour, comment vas-tu ?", CATALOG, skills=SKILLS)
    assert selection.toolsets == (), selection.toolsets
    assert selection.skill is None
    assert intent.describe(selection) == ""


def test_no_phantom_toolset_can_ever_be_returned() -> None:
    """The `mcp__positron__execute_code` class of failure, made impossible."""
    for text in (
        "utilise le serveur positron pour exécuter du code",
        "appelle mcp__positron__execute_code",
        "graphique depuis positron",
    ):
        selection = intent.select(text, CATALOG, skills=SKILLS)
        assert set(selection.toolsets) <= set(CATALOG), (text, selection.toolsets)
        assert "positron" not in " ".join(selection.toolsets)


def test_the_context_survives_one_turn() -> None:
    """« et fais un graphique » after a data turn still carries the topic."""
    history = [
        {"role": "user", "content": "voici mes chiffres de ventes par mois"},
        {"role": "assistant", "content": "Merci, je les garde en tête."},
    ]
    selection = intent.select("et fais un graphique", CATALOG, skills=SKILLS, history=history)
    assert "uhb" in selection.toolsets, selection


def test_filtering_keeps_only_the_selection() -> None:
    selection = intent.select("fais un graphique de ces données", CATALOG, skills=SKILLS)
    kept = intent.filter_toolsets(CATALOG, selection)
    assert set(kept) <= set(selection.toolsets) | set(CATALOG)
    assert "uhb" in kept
    assert len(kept) < len(CATALOG), "offrir tout le catalogue est precisement le defaut"

def test_describe_speaks_to_a_non_developer() -> None:
    selection = intent.select("fais un graphique de ces données", CATALOG, skills=SKILLS)
    line = intent.describe(selection)
    assert line, "une capacite utilisee doit s'annoncer en francais"
    assert "execute_code" not in line and "uhb" not in line, line


def test_the_real_toolset_shape_is_understood() -> None:
    """`build_toolsets()` returns objects with `.tools`, not lists of specs.

    Measured the hard way: the first version of the router crashed on the real
    catalog (`'FileToolset' object is not iterable`) while every unit test passed
    on plain lists. The shape is now part of the contract.
    """

    class FakeToolset:
        def __init__(self, tools):
            self.tools = tools

    catalog = {name: FakeToolset(specs) for name, specs in CATALOG.items()}
    selection = intent.select("fais un graphique de ces données", catalog, skills=SKILLS)
    assert "uhb" in selection.toolsets, selection.toolsets
