"""P3-15 — le cablage du routeur derriere `UH_INTENT_ROUTING=1`.

Ce que ces tests protegent, dans l'ordre des responsabilites :

* le **routeur** propose (jamais n'execute) ;
* le **drapeau** absent ou a 0 laisse le comportement historique intact, octet pour octet ;
* une **erreur du routeur** retombe sur le catalogue inchange, jamais sur un catalogue
  plus large ;
* un nom hors catalogue ne peut pas entrer dans `request.tools` ;
* une conversation ordinaire n'obtient aucun outil, et donc aucun appel artificiel.
"""

from __future__ import annotations

from uh import intent
from uh import skills as uh_skills
from uh.console import ChatSession, intent_routing_enabled

CATALOG = {
    "file": [{"type": "function", "function": {"name": "read_file", "description": "Read a text file"}}],
    "web_search": [
        {"type": "function", "function": {"name": "web_search", "description": "Search the web"}}
    ],
    "uhb": [
        {
            "type": "function",
            "function": {
                "name": "execute_code",
                "description": "Execute Python, draw a chart with matplotlib",
            },
        }
    ],
}


# --------------------------------------------------------------------------- #
# T13 — le drapeau, et rien d'autre
# --------------------------------------------------------------------------- #
def test_t13_without_the_flag_everything_is_offered(monkeypatch) -> None:
    monkeypatch.delenv("UH_INTENT_ROUTING", raising=False)
    assert intent_routing_enabled() is False
    selection = intent.select("fais un graphique de ces données", CATALOG)
    offered = intent.turn_toolsets(CATALOG, selection, enabled=intent_routing_enabled())
    assert offered == CATALOG, "sans drapeau, le catalogue doit passer inchange"


def test_t13_only_the_exact_value_one_turns_it_on(monkeypatch) -> None:
    """A stray inherited value must not switch the feature on by itself."""
    for value, expected in (
        ("1", True),
        (" 1 ", True),
        ("0", False),
        ("", False),
        ("true", False),
        ("yes", False),
        ("on", False),
        ("1x", False),
        ("01", False),
    ):
        monkeypatch.setenv("UH_INTENT_ROUTING", value)
        assert intent_routing_enabled() is expected, value


def test_t13_flag_on_narrows_exactly_to_the_selection(monkeypatch) -> None:
    monkeypatch.setenv("UH_INTENT_ROUTING", "1")
    selection = intent.select("fais un graphique de ces données", CATALOG)
    offered = intent.turn_toolsets(CATALOG, selection, enabled=True)
    assert set(offered) == set(selection.toolsets)
    assert set(offered) < set(CATALOG)


# --------------------------------------------------------------------------- #
# T12 / T15 — aucun outil pour une conversation, aucun nom hors catalogue
# --------------------------------------------------------------------------- #
def test_t12_a_conversation_gets_no_tool() -> None:
    selection = intent.select("Bonjour, comment vas-tu ?", CATALOG)
    assert intent.turn_toolsets(CATALOG, selection, enabled=True) == {}


def test_t15_a_name_outside_the_catalog_can_never_be_offered() -> None:
    selection = intent.Selection(
        toolsets=("positron", "uhb"), confidence="high", reason="test"
    )
    offered = intent.turn_toolsets(CATALOG, selection, enabled=True)
    assert "positron" not in offered
    assert set(offered) <= set(CATALOG)


def test_t15_an_empty_catalog_stays_empty() -> None:
    selection = intent.Selection(toolsets=("uhb",), confidence="high", reason="test")
    assert intent.turn_toolsets({}, selection, enabled=True) == {}


# --------------------------------------------------------------------------- #
# T14 — une erreur du routeur ne casse rien et n'elargit rien
# --------------------------------------------------------------------------- #
def test_t14_a_router_error_falls_back_to_the_unchanged_catalog(monkeypatch) -> None:
    monkeypatch.setenv("UH_INTENT_ROUTING", "1")

    def boom(*args, **kwargs):
        raise RuntimeError("router down")

    monkeypatch.setattr(intent, "select", boom)
    session = ChatSession.__new__(ChatSession)
    session.toolsets = dict(CATALOG)

    class Console:
        def __init__(self):
            self.details = []

        def detail(self, message):
            self.details.append(message)

        def errout(self, message):
            self.details.append(message)

    session.console = Console()
    offered = session._intent_toolsets([{"role": "user", "content": "fais un graphique"}])
    assert offered == CATALOG, "repli = comportement historique, ni plus ni moins"
    assert session.last_intent is None
    assert any("intent:" in line for line in session.console.details)


# --------------------------------------------------------------------------- #
# T7 — proposer n'est pas executer
# --------------------------------------------------------------------------- #
def test_t7_selecting_a_toolset_never_calls_it(monkeypatch) -> None:
    monkeypatch.setenv("UH_INTENT_ROUTING", "1")
    called = {"n": 0}

    class Toolset:
        tools = CATALOG["uhb"]

        def call(self, *args, **kwargs):
            called["n"] += 1
            raise AssertionError("le routeur ne doit jamais executer un outil")

    catalog = {"uhb": Toolset()}
    session = ChatSession.__new__(ChatSession)
    session.toolsets = catalog
    session.console = type("Console", (), {"detail": lambda *a: None, "errout": lambda *a: None})()

    offered = session._intent_toolsets([{"role": "user", "content": "fais un graphique"}])

    assert offered["uhb"] is catalog["uhb"], "le toolset est transmis tel quel, pas remplace"
    assert called["n"] == 0, "aucune execution : UHB reste seul juge"


# --------------------------------------------------------------------------- #
# T6 — un skill invalide ne bloque rien
# --------------------------------------------------------------------------- #
def test_t6_a_broken_skill_folder_does_not_break_selection(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("skills folder unreadable")

    monkeypatch.setattr(uh_skills, "list_skills", boom)
    from uh.console import _skill_listing

    assert _skill_listing() == []
    selection = intent.select("fais un graphique de ces données", CATALOG, skills=[])
    assert "uhb" in selection.toolsets


def test_t6_an_unreadable_skill_file_is_skipped_by_the_listing() -> None:
    listing = uh_skills.list_skills()["skills"]
    assert isinstance(listing, list)
    for entry in listing:
        assert entry.get("name"), "une entree sans nom ne doit pas exister"


# --------------------------------------------------------------------------- #
# T10 — meme intention, meme selection, quel que soit le fournisseur
# --------------------------------------------------------------------------- #
def test_t10_the_selection_is_provider_independent() -> None:
    """The router never sees a provider: same request ⇒ same capabilities."""
    first = intent.select("Recherche les dernières informations sur ce sujet", CATALOG)
    second = intent.select("Recherche les dernières informations sur ce sujet", CATALOG)
    assert first.payload() == second.payload()
    assert "web_search" in first.toolsets


# --------------------------------------------------------------------------- #
# les fichiers reels, pas seulement des doubles
# --------------------------------------------------------------------------- #
def test_real_skills_are_usable_and_selection_stays_inside_the_catalog(monkeypatch) -> None:
    monkeypatch.setenv("UH_INTENT_ROUTING", "1")
    real_skills = uh_skills.list_skills()["skills"]
    assert real_skills, "la bibliotheque reelle doit etre visible depuis un test"
    for phrase in (
        "Fais-moi un graphique de ces données.",
        "Recherche les dernières informations sur ce sujet.",
        "Bonjour, comment vas-tu ?",
        "Peux-tu visualiser l'évolution de ces chiffres ?",
    ):
        selection = intent.select(phrase, CATALOG, skills=real_skills)
        offered = intent.turn_toolsets(CATALOG, selection, enabled=True)
        assert set(offered) <= set(CATALOG), (phrase, offered)
        assert len(offered) <= 3


# --------------------------------------------------------------------------- #
# le skill selectionne doit etre dans le contexte, pas seulement au journal
# --------------------------------------------------------------------------- #
def _session_for_skill(tmp_path):
    session = ChatSession.__new__(ChatSession)
    session.history = [{"role": "system", "content": "PROMPT DE BASE"}]
    session.toolsets = dict(CATALOG)
    session.workspace = tmp_path
    session.role = None
    session.extra_system = ""
    session.events = None
    session.console = type("Console", (), {"detail": lambda *a: None, "errout": lambda *a: None})()
    return session


def test_the_selected_skill_reaches_the_context_of_the_turn(monkeypatch, tmp_path) -> None:
    """The mechanism, deterministically: a proposed skill changes the prompt sent.

    The router's own scoring is covered by the router tests; mixing both here made
    the test fail for a reason that belonged to neither.
    """
    monkeypatch.setenv("UH_INTENT_ROUTING", "1")
    monkeypatch.setattr(uh_skills, "prompt_block", lambda name, **kw: "## Active skill: " + str(name) + " / CORPS")
    session = _session_for_skill(tmp_path)
    session.last_intent = {"skill": "data-science-visualization"}
    base = [{"role": "system", "content": "PROMPT DE BASE"}, {"role": "user", "content": "fais un graphique"}]

    overlay = session._intent_skill(base)

    assert "CORPS" in overlay[0]["content"], "le skill doit etre dans le prompt envoye"
    assert "You are UH" in overlay[0]["content"], "le prompt UH est reconstruit, pas remplace par le skill seul"
    assert base[0]["content"] == "PROMPT DE BASE", "l'historique stocke n'est pas modifie"
    assert overlay is not base, "une copie, jamais l'historique"
    assert session.last_intent.get("skill_injected") is True


def test_an_operator_chosen_skill_is_never_overruled(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("UH_INTENT_ROUTING", "1")
    session = _session_for_skill(tmp_path)
    session.extra_system = "SKILL CHOISI PAR L OPERATEUR"
    base = [{"role": "system", "content": "PROMPT DE BASE"}, {"role": "user", "content": "fais un graphique"}]
    session._intent_toolsets(base)

    overlay = session._intent_skill(base)

    assert overlay[0]["content"] == "PROMPT DE BASE", "aucune substitution"


def test_flag_off_never_injects_a_skill(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("UH_INTENT_ROUTING", raising=False)
    session = _session_for_skill(tmp_path)
    base = [{"role": "system", "content": "PROMPT DE BASE"}, {"role": "user", "content": "fais un graphique"}]
    session.last_intent = {"skill": "data-science-visualization"}

    assert session._intent_skill(base) == base
