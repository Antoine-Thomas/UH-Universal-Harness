"""Which capabilities a turn needs — decided before the model sees the tool list.

Today every turn is offered *every* tool of *every* toolset (``_tool_definitions``
walks the whole catalog), which is the opposite of what the operator asked for:
a plain question should reach the model with no tools at all, and a chart request
should reach it with the data and execution tools, without the user typing a
command.

This module is the deterministic half of that decision. It reads the request, the
recent history and the *actual* catalog, and returns the toolsets worth offering.
It never invents a name: a toolset or a skill it returns always exists in what was
passed in, so a selection cannot create a phantom tool (the `mcp__positron__…`
class of failure).

What it is not: a model. No network, no tokens, no randomness — same input, same
selection, which is what makes it testable and what lets UHB stay the only
authority on execution. The model still decides *whether* to call a tool; it no
longer decides *which tools exist for this question*.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Words that ask for a picture of numbers.
CHART_WORDS = (
    "graphique",
    "graphe",
    "courbe",
    "trace",
    "tracer",
    "visualise",
    "visualiser",
    "chart",
    "plot",
    "diagramme",
    "histogramme",
    "nuage de points",
    "figure",
)
#: Words that ask to look something up outside the machine.
WEB_WORDS = (
    "recherche",
    "rechercher",
    "cherche",
    "chercher",
    "actualite",
    "actualites",
    "news",
    "internet",
    "web",
    "en ligne",
    "dernieres informations",
    "verifie sur",
    "search",
    "look up",
)
#: Words that point at a file on disk or an attachment.
FILE_WORDS = (
    "fichier",
    "dossier",
    "document",
    "pdf",
    "csv",
    "json",
    "texte joint",
    "piece jointe",
    "lis ",
    "lire ",
    "ouvre ",
    "open ",
    "read ",
    "file",
)
#: Words that ask to run something.
EXEC_WORDS = (
    "execute",
    "executer",
    "lance",
    "lancer",
    "script",
    "shell",
    "terminal",
    "commande",
    "python",
    "run ",
)
#: Words that ask about memory / past work.
MEMORY_WORDS = (
    "souviens",
    "souvenir",
    "memoire",
    "note",
    "notes",
    "historique de mes",
    "remember",
    "memory",
)
#: Words that make a question *conversational* rather than actionable.
SMALL_TALK = (
    "bonjour",
    "salut",
    "merci",
    "ca va",
    "comment vas",
    "qui es-tu",
    "hello",
    "good morning",
    "blague",
    "raconte-moi une histoire",
)
#: Words that ask to **see** an existing picture. They point at `web_search`, the
#: toolset that publishes `web_image_search` (G11) : a photo is searched, not drawn.
IMAGE_WORDS = (
    "photo",
    "photos",
    "photographie",
    "image",
    "images",
    "picture",
    "pictures",
)
#: Words that ask to **make** a picture. Those are G13 (generation), not a photo
#: library : without this guard, "dessine un chat photo realiste" would be routed to
#: stock photos and answered with a stranger's cat.
GENERATION_WORDS = (
    "dessine",
    "dessiner",
    "dessin de",
    "peins",
    "peindre",
    "genere",
    "generer",
    "fabrique",
    "invente",
    "draw",
    "generate",
    "paint",
)
#: Fixing code needs to read, write, patch and search: the `file` family, and only it.
CODE_EDIT_WORDS = (
    "corrige",
    "corriger",
    "patch",
    "patche",
    "refactor",
    "refactorise",
    "renomme",
    "renommer",
    "bug",
    "fix ",
)
#: The operator's explicit way back to the whole toolbox. It is a sentence they
#: write, never something the router guesses.
ALL_TOOLS = (
    "tous les outils",
    "toutes les capacites",
    "tout le catalogue",
    "all tools",
    "every tool",
)
#: Météo : la famille entière (FR, EN, accents), jamais la phrase exacte.
WEATHER_WORDS = (
    "quel temps",
    "temps fait",
    "meteo",
    "weather",
    "pleu",
    "pluie",
    "neige",
    "rain",
    "snow",
    "forecast",
    "temperature",
)
#: Radicaux qu'on cherche en sous-chaîne (pleut, pleuvoir, pleuvra, neigeux…).
WEATHER_STEMS = ("pleu", "pluie", "neige", "quel temps", "temps fait")
#: Compter dans un fichier : lire suffit, un terminal n'est pas nécessaire.
COUNT_WORDS = (
    "combien de lignes",
    "nombre de lignes",
    "compte les lignes",
    "how many lines",
    "line count",
)
#: Verbes de lecture — un nom de fichier suffit à en faire une demande de lecture.
READ_VERBS = (
    "lis ",
    "lire ",
    "ouvre ",
    "open ",
    "read ",
    "consulte",
    "contenu de",
    "regarde ",
)
#: Verbes d'écriture — avec une extension, c'est `write_file`, jamais la mémoire.
WRITE_VERBS = (
    "cree",
    "creer",
    "ecris",
    "ecrire",
    "write ",
    "create ",
    "enregistre",
    "save ",
    "redige",
    "rediger",
)
#: Verbes d'exécution : les tests, un script, une commande.
RUN_VERBS = (
    "lance",
    "lancer",
    "execute",
    "executer",
    "demarre",
    "demarrer",
    "run ",
    "pytest",
)
#: Lister un dossier : `search_files` suffit.
LIST_WORDS = ("liste", "list ", "quels fichiers", "quels sont les fichiers")
LIST_OBJECTS = ("dossier", "fichier", "repertoire", "folder", "directory")
#: Questions de culture générale : le modèle répond, aucun outil n'aide.
CULTURE_WORDS = ("capitale", "qui a ecrit", "definition de", "combien font", "signifie")
#: Demandes d'explication : une réponse en mots, pas un fichier.
EXPLAIN_WORDS = ("explique", "expliquer", "explain")
#: « où est enregistré X » est une lecture, même avec un verbe d'écriture.
WHERE_IS = re.compile(r"\bou est\b|\bou se trouve\b|\bwhere is\b|\bwhere does\b")
#: Un nom de fichier réel : au moins une extension courte, pas un point de phrase.
FILE_NAME = re.compile(r"[\w-]+\.[A-Za-z0-9]{1,5}(?![\w])")
#: Ce qu'une demande **non reconnue** reçoit : un petit socle en lecture seule.
#: Jamais d'écriture, jamais de terminal, jamais la mémoire (décision de Thomas).
FLOOR_TOOLS = ("read_file", "search_files", "web_search")
#: Marqueur de conversation positivement reconnue — la seule route vers zéro outil.
CONVERSATION_PREFIX = "conversation"
#: Below this many characters, a request with no strong signal is ambiguous.
SHORT_REQUEST = 24
#: Toolsets are capped: offering twelve tools "just in case" is the defect.
MAX_TOOLSETS = 3
#: How many times the router had to refuse a tool it had withheld this turn, per
#: tool name. A refusal is not an error: it is the measure of a router that is too
#: narrow, and the number the operator asked for before trusting it.
_REFUSALS: Dict[str, int] = {}


def _fold(text: str) -> str:
    """Lowercase, accents stripped: 'graphique' matches 'Graphique' and 'GRÀPHIQUE'."""
    blob = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in blob if not unicodedata.combining(ch)).lower()


def _words(text: str) -> List[str]:
    return [word for word in re.split(r"[^a-z0-9]+", _fold(text)) if len(word) > 2]


def _hits(text: str, needles: Iterable[str]) -> int:
    """Substring count — pour les **radicaux** seulement (« pleu » couvre pleut/pleuvoir)."""
    folded = _fold(text)
    return sum(1 for needle in needles if _fold(needle) in folded)


def _word_hits(text: str, needles: Iterable[str]) -> bool:
    """Un mot entier, pas une sous-chaîne.

    « photosynthese » n'est pas une « photo », « l'histoire » ne dit pas « liste »,
    « training » n'est pas de la « rain » : sans bornes de mot, une famille attrape
    des mots qu'elle ne connaît pas. Les radicaux qui doivent rester larges
    (`pleu`, `pluie`) passent par `_hits`, jamais par ici.
    """
    folded = _fold(text)
    for needle in needles:
        stem = _fold(needle).strip()
        if not stem:
            continue
        pattern = r"(?<![a-z0-9])" + re.escape(stem) + r"(?![a-z0-9])"
        if re.search(pattern, folded):
            return True
    return False


@dataclass(frozen=True)
class Selection:
    """What a turn is offered, and why. ``ask`` set means: ask before acting."""

    toolsets: Tuple[str, ...] = ()
    #: Toolsets qu'une famille d'action reconnue exige et que le catalogue n'offre pas.
    unavailable: tuple[str, ...] = ()
    skill: Optional[str] = None
    confidence: str = "none"  # "high" | "medium" | "none"
    reason: str = ""
    ask: Optional[str] = None
    scores: Mapping[str, int] = field(default_factory=dict)
    #: Restriction **outil par outil** d'une famille reconnue (« lis README.md » →
    #: `read_file` seul). Vide = l'offre se fait par toolset, comme avant.
    tools: Tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.toolsets and not self.skill and not self.tools

    def payload(self) -> Dict[str, Any]:
        """For the decision journal — no secret, no tool arguments."""
        return {
            "toolsets": list(self.toolsets),
            "tools": list(self.tools),
            "skill": self.skill,
            "confidence": self.confidence,
            "reason": self.reason,
            "ask": self.ask,
            "scores": dict(self.scores),
        }


def _toolset_vocabulary(name: str, specs: Any) -> str:
    """The words a toolset can be recognised by, from either shape the code uses.

    ``build_toolsets()`` returns *Toolset objects* (``.tools``), while a test or a
    panel export may hand over a plain list of specs. Both are accepted: a router
    that only speaks one of them is a router that silently selects nothing.
    """
    entries = getattr(specs, "tools", None)
    if entries is None:
        entries = specs if isinstance(specs, (list, tuple)) else ()
    parts = [name]
    for spec in entries or ():
        if not isinstance(spec, Mapping):
            continue
        inner = spec.get("function")
        function: Mapping[str, Any] = inner if isinstance(inner, Mapping) else spec
        parts.append(str(function.get("name") or ""))
        parts.append(str(function.get("description") or ""))
        schema = function.get("parameters")
        if isinstance(schema, Mapping):
            parts.append(" ".join(str(key) for key in (schema.get("properties") or {})))
    return " ".join(parts)


def _family_boost(name: str, vocabulary: str, text: str) -> int:
    """Family score: the request's words against what this toolset can actually do."""
    boost = 0
    if _hits(text, CHART_WORDS):
        if any(
            mark in vocabulary
            for mark in ("execute_code", "chart", "plot", "matplotlib", "figure", "python")
        ):
            boost += 6
    if _hits(text, WEB_WORDS):
        if any(
            mark in vocabulary
            for mark in ("web_search", "search the web", "web", "internet", "http")
        ):
            boost += 6
    if _hits(text, FILE_WORDS):
        if any(
            mark in vocabulary
            for mark in ("read_file", "list_directory", "file", "path", "document")
        ):
            boost += 5
    if _hits(text, EXEC_WORDS):
        if any(
            mark in vocabulary for mark in ("terminal", "shell", "execute", "command", "python")
        ):
            boost += 5
    if _hits(text, MEMORY_WORDS):
        if any(mark in vocabulary for mark in ("memory", "remember", "note", "graph")):
            boost += 4
    if _hits(text, IMAGE_WORDS) and not _hits(text, GENERATION_WORDS):
        # Une photo existante se cherche : le toolset qui publie `web_image_search`.
        if "web_image_search" in vocabulary:
            boost += 6
    if _hits(text, CODE_EDIT_WORDS):
        # Corriger du code, c'est lire, écrire et patcher — pas dessiner un graphe.
        if any(mark in vocabulary for mark in ("read_file", "write_file", "patch")):
            boost += 6
    return boost


#: Mots et tournures d'une relance : la demande est dans le tour d'avant.
FOLLOW_UP_WORDS = (
    "oui",
    "yes",
    "ok",
    "d'accord",
    "vas-y",
    "vas y",
    "go",
    "continue",
    "encore",
    "refais",
    "remets",
    "pareil",
    "idem",
    "fais-le",
    "fais le",
    "montre-moi",
    "montre moi",
    "and tomorrow",
    "et demain",
    "celui-la",
    "celle-la",
    "exactement",
    "please",
)


def _looks_like_follow_up(text: str) -> bool:
    """Vrai pour un message court, sans objet propre, ou une anaphore.

    Un nom de fichier, une salutation ou une question de culture générale ne sont
    jamais des relances : ils ont leur propre route. Le reste — six mots au plus —
    est une relance si un mot l'annonce ou si la phrase est trop courte pour
    porter un objet.
    """
    if not text:
        return False
    if FILE_NAME.search(text):
        return False
    if _hits(text, SMALL_TALK) or _word_hits(text, CULTURE_WORDS + EXPLAIN_WORDS):
        return False
    # Un objet d'action, même en trois mots, n'est pas une relance : « fais un
    # graphique » demande un graphique, il n'hérite de rien.
    if _word_hits(text, CHART_WORDS + WEB_WORDS + EXEC_WORDS + COUNT_WORDS + WEATHER_WORDS):
        return False
    if len(_words(text)) > 6:
        return False
    if _word_hits(text, FOLLOW_UP_WORDS):
        return True
    return len(text.strip()) < SHORT_REQUEST


def _score_toolsets(
    text: str,
    catalog: Mapping[str, Sequence[Any]],
    words: Sequence[str],
) -> Dict[str, int]:
    scores: Dict[str, int] = {}
    for name, specs in catalog.items():
        vocabulary = _fold(_toolset_vocabulary(name, specs))
        score = _family_boost(name, vocabulary, text)
        # Cheap lexical overlap with the catalog's own words (tool names, descriptions).
        score += sum(2 for word in set(words) if word in vocabulary)
        if score:
            scores[str(name)] = score
    return scores


def _match_skill(
    text: str,
    skills: Sequence[Mapping[str, Any]],
    words: Sequence[str],
) -> Optional[Mapping[str, Any]]:
    """Best skill by weighted lexical overlap, or ``None`` when nothing is convincing.

    A wrong skill is worse than no skill: it injects a whole protocol into the
    system prompt. Measured on the real library (50 skills): scoring every field
    the same way selected `blender-physics-mantaflow-sim` for "recherche les
    dernières informations" — the shared word was "rendu". So the name and the
    title weigh more than the description, at least two distinct request words
    must match, and the threshold is high.
    """
    best: Optional[Mapping[str, Any]] = None
    best_score = 0
    for entry in skills or ():
        name = str(entry.get("name") or "")
        title = str(entry.get("title") or "")
        description = str(entry.get("description") or "")
        if not name:
            continue
        name_words = set(_words(name.replace("-", " ")))
        title_words = set(_words(title))
        description_words = set(_words(description))
        request_words = set(words)
        score = 0
        matches = 0
        for word in request_words:
            if word in name_words:
                score += 4
                matches += 1
            if word in title_words:
                score += 4
                matches += 1
            elif word in description_words:
                score += 1
                matches += 1
        if "graph" in name or "visual" in name or "chart" in name or "data-science" in name:
            family = 8 * _hits(text, CHART_WORDS)
        elif "rag" in name or "literature" in name:
            family = 8 * _hits(text, ("rag", "documentaire", "sources", "recherche documentaire"))
        else:
            family = 0
        total = score + family
        if total > best_score and (family or (score >= 12 and matches >= 2)):
            best, best_score = entry, total
    if best is None or best_score < 8:
        return None
    return best


def select(
    text: str,
    catalog: Mapping[str, Sequence[Any]],
    *,
    skills: Sequence[Mapping[str, Any]] = (),
    history: Sequence[Mapping[str, Any]] = (),
    has_attachment: bool = False,
    inherit: bool = True,
) -> Selection:
    """Pick the toolsets (and at most one skill) this request is worth.

    Rules, in order:
    1. small talk or a request too short with no signal → no tool at all;
    2. a skill's own vocabulary matches strongly → the skill is proposed;
    3. toolsets are scored on their real vocabulary plus family keywords, capped;
    4. nothing above the threshold → conversational answer, never an artificial call;
    5. two families tied on a short request → ask, do not choose arbitrarily.
    """
    request = (text or "").strip()
    if not request:
        return Selection(reason="demande vide")

    # La porte de sortie explicite : l'operateur demande tout le catalogue, le
    # routeur ne le devine pas. C'est une phrase, testee, et elle ne s'invente pas.
    if _hits(request, ALL_TOOLS):
        return Selection(
            toolsets=tuple(str(name) for name in catalog),
            confidence="high",
            reason="tous les outils demandés",
        )

    # ------------------------------------------------------------------ #
    # Familles reconnues : des **règles**, jamais des phrases. Chacune nomme
    # exactement les outils qu'elle demande — les tests de généralité en donnent
    # des paraphrases que ces règles n'ont jamais vues.
    # ------------------------------------------------------------------ #
    names_a_file = bool(FILE_NAME.search(request))
    # The topic survives one turn: "et fais un graphique" after "voici mes chiffres"
    # still carries the data context, without re-injecting old tool calls.
    recent = " ".join(
        str(message.get("content") or "")
        for message in list(history or ())[-4:]
        if isinstance(message, Mapping) and message.get("role") in ("user", "assistant")
    )
    context = f"{request} {recent}".strip()
    words = _words(request) or _words(context)

    if _word_hits(request, GENERATION_WORDS):
        # Fabriquer une image, c'est G13 : aucun outil ici, et le chat le dit.
        return Selection(
            confidence="none",
            reason=f"{CONVERSATION_PREFIX} : demande de fabrication d'image (G13)",
        )
    if names_a_file and WHERE_IS.search(_fold(request)):
        # « où est enregistré keys.toml ? » est une lecture, malgré « enregistre ».
        return Selection(
            toolsets=("file",),
            tools=("read_file", "search_files"),
            confidence="high",
            reason="localiser un fichier = le lire",
        )
    if _word_hits(request, WEATHER_WORDS) or _hits(request, WEATHER_STEMS):
        return Selection(
            toolsets=("web_search",),
            tools=("web_search",),
            confidence="high",
            reason="météo = recherche web",
        )
    if _word_hits(request, COUNT_WORDS):
        return Selection(
            toolsets=("file",),
            tools=("read_file", "search_files"),
            confidence="high",
            reason="compter des lignes = lire, pas exécuter",
        )
    if (
        _word_hits(request, RUN_VERBS)
        and not _word_hits(request, WRITE_VERBS)
        and not _word_hits(request, READ_VERBS)
    ):
        return Selection(
            toolsets=("terminal",),
            tools=("terminal",),
            confidence="high",
            reason="exécution demandée",
        )
    if names_a_file and _word_hits(request, WRITE_VERBS):
        return Selection(
            toolsets=("file",),
            tools=("write_file",),
            confidence="high",
            reason="écriture dans un fichier nommé",
        )
    if names_a_file and _word_hits(request, READ_VERBS):
        return Selection(
            toolsets=("file",),
            tools=("read_file",),
            confidence="high",
            reason="lecture d'un fichier nommé",
        )
    if _word_hits(request, LIST_WORDS) and _word_hits(request, LIST_OBJECTS):
        return Selection(
            toolsets=("file",),
            tools=("search_files",),
            confidence="high",
            reason="lister un dossier",
        )
    if _word_hits(request, IMAGE_WORDS):
        return Selection(
            toolsets=("web_search",),
            tools=("web_image_search",),
            confidence="high",
            reason="photo existante à chercher",
        )
    if _word_hits(request, WEB_WORDS):
        return Selection(
            toolsets=("web_search",),
            tools=("web_search",),
            confidence="high",
            reason="recherche web",
        )
    if _word_hits(request, CULTURE_WORDS) or _word_hits(request, EXPLAIN_WORDS):
        return Selection(
            confidence="none", reason=f"{CONVERSATION_PREFIX} : réponse en mots, aucun outil"
        )
    # Une relance (« oui », « vas-y », « et demain ? », « refais-le ») n'a pas
    # d'objet propre : elle demande la même capacité que le tour précédent de
    # l'utilisateur. Sans tour précédent, il n'y a rien à poursuivre.
    if inherit and _looks_like_follow_up(request):
        earlier = [
            str(message.get("content") or "")
            for message in history or ()
            if isinstance(message, Mapping) and message.get("role") == "user"
        ]
        earlier = [text for text in earlier if text.strip() and text.strip() != request]
        if not earlier:
            return Selection(
                confidence="none",
                reason=f"{CONVERSATION_PREFIX} : relance sans tour précédent, rien à poursuivre",
            )
        prior = select(earlier[-1], catalog, skills=skills, history=(), inherit=False)
        if prior.tools or prior.toolsets:
            return Selection(
                toolsets=prior.toolsets,
                tools=prior.tools,
                skill=prior.skill,
                confidence="high",
                reason=f"relance : hérite de l'offre du tour précédent ({prior.reason})",
            )
        return Selection(
            confidence="low",
            reason="relance sans offre à poursuivre : socle lecture seule",
        )

    # Une courte phrase sans objet (fichier, web, exécution, mémoire, image) est
    # une conversation : c'est la seule route vers « zéro outil ».
    if len(request) < SHORT_REQUEST and not names_a_file:
        actionable = (
            WEB_WORDS
            + EXEC_WORDS
            + MEMORY_WORDS
            + IMAGE_WORDS
            + CODE_EDIT_WORDS
            + WEATHER_WORDS
            + COUNT_WORDS
            + CHART_WORDS
        )
        if not _word_hits(context, actionable):
            return Selection(
                confidence="none", reason=f"{CONVERSATION_PREFIX} : courte phrase sans objet"
            )

    # Un nom n'est pas une tâche : sans verbe d'action (lire, écrire, lancer,
    # lister, dessiner un graphique, corriger), la demande n'est pas reconnue et
    # reçoit le socle en lecture seule — même si un mot (« notes », « dossier »)
    # suffisait à faire scorer un toolset, y compris un serveur MCP.
    action_verbs = READ_VERBS + WRITE_VERBS + RUN_VERBS + LIST_WORDS + CHART_WORDS + CODE_EDIT_WORDS
    if not _word_hits(request, action_verbs) and not _hits(request, SMALL_TALK):
        return Selection(
            confidence="low",
            reason="socle lecture seule : aucun verbe d'action reconnu",
        )

    if (
        _hits(request, SMALL_TALK)
        and len(words) <= 6
        and not _hits(request, CHART_WORDS + WEB_WORDS + EXEC_WORDS)
    ):
        return Selection(reason="conversation : aucune capacite requise")

    scores = _score_toolsets(context, catalog, words)
    if has_attachment and "file" in catalog:
        scores["file"] = scores.get("file", 0) + 4

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    threshold = 5
    chosen = [name for name, score in ranked if score >= threshold][:MAX_TOOLSETS]
    skill_entry = _match_skill(context, skills, words)
    skill = str(skill_entry.get("name")) if skill_entry else None

    if len(request) < SHORT_REQUEST and len(chosen) > 1:
        top_two = [score for _name, score in ranked[:2]]
        if len(top_two) == 2 and top_two[0] == top_two[1]:
            return Selection(
                confidence="none",
                reason="deux capacites candidates a egalite sur une demande courte",
                ask="Voulez-vous que je regarde un fichier precis, ou que je cherche sur internet ?",
                scores=scores,
            )

    # Les capacites declarees par la Skill retenue renforcent une selection deja
    # pertinente — elles ne la creent pas. Un nom hors catalogue est ignore : c'est
    # `turn_toolsets` qui intersecte, ici on ne fait que ponderer.
    if skill:
        declared = [str(item) for item in (skill_entry or {}).get("capabilities") or []]
        available = [name for name in declared if name in catalog]
        ignored = [name for name in declared if name not in catalog]
        if available:
            scores = {**scores}
            for name in available:
                # La skill a ete retenue sur ses mots : ses capacites proposes
                # suffisent a faire entrer le toolset, l'intersection avec le catalogue
                # restant obligatoire (turn_toolsets).
                scores[name] = max(scores.get(name, 0) + 3, threshold)
            ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
            chosen = [name for name, value in ranked if value >= threshold][:MAX_TOOLSETS]
        if ignored:
            reason_parts_extra = " | capacites ignorees (hors catalogue) : " + ", ".join(ignored)
        else:
            reason_parts_extra = ""
    else:
        reason_parts_extra = ""

    confidence = "high" if (ranked and ranked[0][1] >= 8) or skill else "medium"
    reason_parts = []
    if chosen:
        reason_parts.append("outils : " + ", ".join(chosen))
    if skill:
        reason_parts.append(f"skill : {skill}")
    missing: list = []
    action_words = tuple(CHART_WORDS) + ("code", "python", "calcul", "calcule", "execute")
    if _hits(text, action_words) and "uhb" not in catalog:
        missing.append("uhb")
    if reason_parts_extra:
        reason_parts.append(reason_parts_extra)
    return Selection(
        toolsets=tuple(chosen),
        skill=skill,
        confidence=confidence,
        reason=" | ".join(reason_parts),
        unavailable=tuple(missing),
        scores=scores,
    )


def filter_toolsets(
    toolsets: Mapping[str, Any],
    selection: Selection,
) -> Dict[str, Any]:
    """The catalog restricted to the selection (empty selection → empty catalog)."""
    if not selection.toolsets:
        return {}
    return {name: toolset for name, toolset in toolsets.items() if name in selection.toolsets}


def _spec_name(spec: Any) -> str:
    if not isinstance(spec, Mapping):
        return ""
    inner = spec.get("function")
    function: Mapping[str, Any] = inner if isinstance(inner, Mapping) else spec
    return str(function.get("name") or "")


class OfferedToolset:
    """Un toolset portant la vue **d'un tour** : les outils nommés, rien d'autre.

    Un proxy, comme la vue lecture seule du mode : même objet sous-jacent, liste
    d'outils réduite. C'est la seule façon d'offrir `read_file` sans `write_file`
    quand la décision est prise outil par outil ; pour une sélection par toolset,
    l'objet est transmis tel quel (test de contrat).
    """

    def __init__(self, toolset: Any, names: frozenset[str], reason: str) -> None:
        self._toolset = toolset
        self._reason = reason
        self.name = str(getattr(toolset, "name", "") or "")
        offered = [spec for spec in (getattr(toolset, "tools", None) or [])]
        self.tools = [spec for spec in offered if _spec_name(spec) in names]
        self._withheld = {_spec_name(spec) for spec in offered} - set(names)

    def call(self, name: str, args: Dict[str, Any]) -> dict:
        result = self._toolset.call(name, args)
        return result if isinstance(result, dict) else {"result": result}

    def explain_missing(self, name: str) -> Optional[dict]:
        if name in self._withheld:
            return {
                "error": (
                    f"`{name}` n'est pas offert pour ce tour ({self._reason}). "
                    "Dis « avec tous les outils » pour que je les repropose."
                ),
                "type": "forbidden",
                "tool": name,
                "reason": "not_offered",
                "retry": "avec tous les outils",
            }
        explain = getattr(self._toolset, "explain_missing", None)
        return explain(name) if callable(explain) else None


def offer_tools(catalog: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    """The catalogue reduced to ``names``, toolset by toolset (proxy, jamais mutation)."""
    wanted = frozenset(str(name) for name in names)
    kept: Dict[str, Any] = {}
    for toolset_name, toolset in catalog.items():
        view = OfferedToolset(toolset, wanted, "outils retenus pour cette demande")
        if view.tools:
            kept[str(toolset_name)] = view
    return kept


def turn_toolsets(
    toolsets: Mapping[str, Any],
    selection: Optional[Selection],
    *,
    enabled: bool,
) -> Dict[str, Any]:
    """What this turn actually offers — the one place the policy is decided.

    * flag off (or no selection, or the router raised) → **unchanged catalogue**:
      the historical behaviour, byte for byte, because a router that fails must
      never take a capability away from the model;
    * a recognised family naming **tools** → those tools and their toolsets only;
    * a recognised family naming **toolsets** → those toolsets, intersected with
      what really exists (a name outside the catalogue cannot widen anything);
    * a positively recognised **conversation** → no tool at all;
    * anything else → the **read-only floor** (`read_file`, `search_files`,
      `web_search`): never a write, never a terminal, never memory. That is the
      operator's decision: "zéro outil" is reserved for recognised conversation,
      and an unrecognised request must still be able to look.

    A tool withheld here is not lost: `explain_withheld` and the proxy's own
    `explain_missing` name it and give the sentence that brings it back.
    """
    catalog = dict(toolsets)
    if not enabled or selection is None:
        return catalog
    if selection.ask:
        # Ambiguity is surfaced as a question; the turn keeps the historical
        # catalogue rather than guessing on the operator's behalf.
        return catalog
    if selection.tools:
        return offer_tools(catalog, selection.tools)
    if selection.toolsets:
        # `kept` empty means the selection named nothing that exists: offer
        # nothing, never the whole catalogue — widening is the defect.
        return filter_toolsets(catalog, selection) or {}
    if str(selection.reason or "").startswith(CONVERSATION_PREFIX):
        return {}
    return offer_tools(catalog, FLOOR_TOOLS)


def describe(selection: Selection) -> str:
    """One plain-French line for the interface — no internal tool names."""
    if selection.ask:
        return selection.ask
    if not selection.toolsets and not selection.skill and not selection.tools:
        return ""
    labels = {
        "file": "Lecture des documents",
        "terminal": "Exécution de commandes",
        "web_search": "Recherche documentaire",
        "memory": "Consultation de la mémoire",
        "uhb": "Analyse des données et génération du graphique",
    }
    tool_labels = {
        "read_file": "Lecture du fichier",
        "write_file": "Écriture du fichier",
        "search_files": "Recherche dans les fichiers",
        "terminal": "Exécution d'une commande",
        "web_search": "Recherche web",
        "web_image_search": "Recherche d'images",
    }
    parts = [labels.get(name, "Traitement") for name in selection.toolsets]
    if not parts and selection.tools:
        parts = [tool_labels.get(name, "Traitement") for name in selection.tools]
    if selection.skill:
        parts.append("application de la méthode adaptée")
    return " · ".join(dict.fromkeys(parts)) + "…"


def _tools_by_toolset(catalog: Mapping[str, Any]) -> Dict[str, str]:
    """Chaque outil du catalogue, et le toolset qui le publie (nom pour nom)."""
    owners: Dict[str, str] = {}
    for toolset_name, specs in catalog.items():
        entries = getattr(specs, "tools", None)
        if entries is None:
            entries = specs if isinstance(specs, (list, tuple)) else ()
        for spec in entries or ():
            if not isinstance(spec, Mapping):
                continue
            inner = spec.get("function")
            function: Mapping[str, Any] = inner if isinstance(inner, Mapping) else spec
            tool = str(function.get("name") or "")
            if tool:
                owners[tool] = str(toolset_name)
    return owners


def explain_withheld(
    name: str,
    catalog: Mapping[str, Any],
    selection: Optional[Selection],
) -> Optional[dict]:
    """Pourquoi cet outil n'est pas offert ce tour, et la phrase qui le ramène.

    Rend ``None`` quand le routeur n'est pas la cause : un nom inconnu reste
    inconnu, et un outil réellement offert n'est pas refusé par ce module. Chaque
    refus est compté : c'est la mesure du risque annoncé (un routeur trop étroit).
    """
    tool = str(name or "")
    if not tool:
        return None
    owner = _tools_by_toolset(catalog).get(tool)
    if owner is None:
        return None
    offered = tuple(selection.toolsets if selection is not None else ())
    if owner in offered:
        return None
    _REFUSALS[tool] = _REFUSALS.get(tool, 0) + 1
    kept = ", ".join(sorted(offered)) or "aucune capacité"
    return {
        "error": (
            f"`{tool}` n'est pas offert pour ce tour : le routeur a retenu {kept} "
            "pour cette demande. Dis « avec tous les outils » pour que je les "
            "repropose, ou précise ta demande."
        ),
        "type": "forbidden",
        "tool": tool,
        "reason": "not_offered",
        "retry": "avec tous les outils",
        "offered": sorted(offered),
    }


def reset_refusals() -> None:
    """Remet le compteur à zéro (une mesure, pas une permission)."""
    _REFUSALS.clear()


def refusal_count(name: str) -> int:
    return _REFUSALS.get(str(name or ""), 0)


def refusal_counts() -> Dict[str, int]:
    """Les refus de la session, outil par outil : le chiffre à suivre."""
    return dict(_REFUSALS)


#: Verbes qui demandent de **fabriquer** une image (G13), distincts des mots
#: d'image : « dessine un chat » n'est pas « trouve la photo d'un chat ».
IMAGE_MAKING_VERBS = ("dessine", "dessiner", "peins", "peindre", "draw ", "paint ")

#: Une phrase, deux options, aucune action. G13 remplacera ce texte par un vrai
#: choix de capacités (registre, coût, VRAM mesurée) ; d'ici là, le chat le dit.
GENERATION_NOTICE = (
    "Je ne fabrique pas d'image : aucune capacité de génération n'est installée. "
    "Deux options : « cherche une photo de chat » pour une vraie photo libre de "
    "droits, ou installer un modèle de diffusion plus tard — rien n'est lancé ici."
)


def wants_generation(text: str) -> bool:
    """Vrai quand la demande est de *fabriquer* une image, pas d'en trouver une."""
    if _hits(text, IMAGE_MAKING_VERBS):
        return True
    return _hits(text, ("genere", "generer", "generate", "fabrique", "invente")) > 0 and (
        _hits(text, IMAGE_WORDS) > 0
    )


def generation_notice(text: str) -> Optional[str]:
    """La phrase déterministe, ou ``None`` : aucun moteur, aucune installation."""
    return GENERATION_NOTICE if wants_generation(text) else None
