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
    "graphique", "graphe", "courbe", "trace", "tracer", "visualise", "visualiser",
    "chart", "plot", "diagramme", "histogramme", "nuage de points", "figure",
)
#: Words that ask to look something up outside the machine.
WEB_WORDS = (
    "recherche", "rechercher", "cherche", "chercher", "actualite", "actualites",
    "news", "internet", "web", "en ligne", "dernieres informations", "verifie sur",
    "search", "look up",
)
#: Words that point at a file on disk or an attachment.
FILE_WORDS = (
    "fichier", "dossier", "document", "pdf", "csv", "json", "texte joint",
    "piece jointe", "lis ", "lire ", "ouvre ", "open ", "read ", "file",
)
#: Words that ask to run something.
EXEC_WORDS = (
    "execute", "executer", "lance", "lancer", "script", "shell", "terminal",
    "commande", "python", "run ",
)
#: Words that ask about memory / past work.
MEMORY_WORDS = (
    "souviens", "souvenir", "memoire", "note", "notes", "historique de mes",
    "remember", "memory",
)
#: Words that make a question *conversational* rather than actionable.
SMALL_TALK = (
    "bonjour", "salut", "merci", "ca va", "comment vas", "qui es-tu", "hello",
    "good morning", "blague", "raconte-moi une histoire",
)
#: Below this many characters, a request with no strong signal is ambiguous.
SHORT_REQUEST = 24
#: Toolsets are capped: offering twelve tools "just in case" is the defect.
MAX_TOOLSETS = 3


def _fold(text: str) -> str:
    """Lowercase, accents stripped: 'graphique' matches 'Graphique' and 'GRÀPHIQUE'."""
    blob = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in blob if not unicodedata.combining(ch)).lower()


def _words(text: str) -> List[str]:
    return [word for word in re.split(r"[^a-z0-9]+", _fold(text)) if len(word) > 2]


def _hits(text: str, needles: Iterable[str]) -> int:
    folded = _fold(text)
    return sum(1 for needle in needles if _fold(needle) in folded)


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

    @property
    def empty(self) -> bool:
        return not self.toolsets and not self.skill

    def payload(self) -> Dict[str, Any]:
        """For the decision journal — no secret, no tool arguments."""
        return {
            "toolsets": list(self.toolsets),
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
        if any(mark in vocabulary for mark in ("execute_code", "chart", "plot", "matplotlib", "figure", "python")):
            boost += 6
    if _hits(text, WEB_WORDS):
        if any(mark in vocabulary for mark in ("web_search", "search the web", "web", "internet", "http")):
            boost += 6
    if _hits(text, FILE_WORDS):
        if any(mark in vocabulary for mark in ("read_file", "list_directory", "file", "path", "document")):
            boost += 5
    if _hits(text, EXEC_WORDS):
        if any(mark in vocabulary for mark in ("terminal", "shell", "execute", "command", "python")):
            boost += 5
    if _hits(text, MEMORY_WORDS):
        if any(mark in vocabulary for mark in ("memory", "remember", "note", "graph")):
            boost += 4
    return boost


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

    # The topic survives one turn: "et fais un graphique" after "voici mes chiffres"
    # still carries the data context, without re-injecting old tool calls.
    recent = " ".join(
        str(message.get("content") or "")
        for message in list(history or ())[-4:]
        if isinstance(message, Mapping) and message.get("role") in ("user", "assistant")
    )
    context = f"{request} {recent}".strip()
    words = _words(request) or _words(context)

    if _hits(request, SMALL_TALK) and len(words) <= 6 and not _hits(request, CHART_WORDS + WEB_WORDS + EXEC_WORDS):
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
    * flag on and the router named toolsets → only those, intersected with what
      really exists (a name outside the catalogue cannot widen anything);
    * flag on and the router named nothing → **no tool at all**: a plain question
      should not arrive with a toolbox.
    """
    catalog = dict(toolsets)
    if not enabled or selection is None:
        return catalog
    if selection.ask:
        # Ambiguity is surfaced as a question; the turn keeps the historical
        # catalogue rather than guessing on the operator's behalf.
        return catalog
    if selection.toolsets:
        kept = filter_toolsets(catalog, selection)
        return kept or catalog
    if selection.confidence == "none" and selection.reason.startswith("conversation"):
        return {}
    return catalog


def describe(selection: Selection) -> str:
    """One plain-French line for the interface — no internal tool names."""
    if selection.ask:
        return selection.ask
    if not selection.toolsets and not selection.skill:
        return ""
    labels = {
        "file": "Lecture des documents",
        "terminal": "Exécution de commandes",
        "web_search": "Recherche documentaire",
        "memory": "Consultation de la mémoire",
        "uhb": "Analyse des données et génération du graphique",
    }
    parts = [labels.get(name, "Traitement") for name in selection.toolsets]
    if selection.skill:
        parts.append("application de la méthode adaptée")
    return " · ".join(dict.fromkeys(parts)) + "…"
