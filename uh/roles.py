"""Character & role: the posture a conversation answers with (P3-14).

A role is *who answers and in what voice*: posture, expertise, tone. It is
deliberately **not** a skill: skills say how a task is executed and stay attached
to the toolsets, while a role changes nothing but the paragraph appended to the
system prompt. The independence proof lives in ``tests/test_roles.py`` — the skills
files, the tool schemas, the MCP configuration and the active toolsets must be
byte-identical before and after a role change.

Stored per session in ``sessions.metadata["role"]`` (no schema migration), so a
project reopened tomorrow answers the way it was left.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

#: Free text is bounded: a role rides in every turn's system prompt, so it cannot
#: be a place to paste a novel (and every token there is paid on every call).
MAX_LABEL = 60
MAX_POSTURE = 800


@dataclass(frozen=True)
class Role:
    """One preset. ``label`` is what the operator reads, ``posture`` what the model reads."""

    id: str
    label: str
    posture: str


#: The presets, in the order the interface shows them. Two of them are this
#: machine's own work (home automation, vision/video) because that is what the
#: operator actually does with it.
PRESETS: tuple[Role, ...] = (
    Role(
        "architecte",
        "Architecte logiciel senior",
        "Answer as a senior software architect: name the trade-offs and the constraints "
        "first, prefer the smallest structure that holds, say what you would refuse to "
        "build and why. Concrete over exhaustive.",
    ),
    Role(
        "fullstack",
        "Développeur full-stack",
        "Answer as a hands-on full-stack developer: give the actual edit, command or code, "
        "assume the reader will paste it, and state the one thing most likely to break.",
    ),
    Role(
        "analyste",
        "Analyste de données",
        "Answer as a data analyst: quantify, name the unit and the source, prefer a table "
        "or a chart over prose, and say plainly when the data cannot support the claim.",
    ),
    Role(
        "redacteur",
        "Rédacteur produit concis",
        "Answer as a concise product writer: short sentences, no filler, the user's benefit "
        "before the feature, one idea per paragraph.",
    ),
    Role(
        "securite",
        "Auditeur sécurité",
        "Answer as a security auditor: state the threat, the exposure and the cheapest fix, "
        "fail closed, and never claim a control works without the measurement that shows it.",
    ),
    Role(
        "domotique",
        "Domotique / Home Assistant",
        "Answer as a home-automation engineer: think in entities, services and automations, "
        "prefer local-first and reversible changes, and warn before anything that can act on "
        "the house at scale.",
    ),
    Role(
        "vision",
        "Vision / vidéo IA",
        "Answer as a computer-vision and video pipeline engineer: think in frames, resolution, "
        "VRAM and wall-clock time, give the measured numbers, and say which licence and which "
        "weights a step depends on.",
    ),
)

BY_ID: Dict[str, Role] = {role.id: role for role in PRESETS}
#: Saint-Exupéry's line applies to prompts: the default is no role at all.
NONE_LABEL = "Aucun rôle (réponse neutre)"


def presets_payload() -> List[Dict[str, str]]:
    """What the interface renders: id + label, never the posture text it does not show."""
    return [{"id": role.id, "label": role.label} for role in PRESETS]


def normalise(raw: Any) -> Optional[Dict[str, str]]:
    """A storable role from what a client sent, or ``None``.

    Accepts a preset by id (the posture is taken from this module, never from the
    client: a client could otherwise smuggle any text into the system prompt), or a
    free-text role with its own label and posture, both bounded.
    """
    if raw in (None, "", "none", "aucun"):
        return None
    if isinstance(raw, str):
        role = BY_ID.get(raw)
        return {"id": role.id, "label": role.label, "posture": role.posture} if role else None
    if not isinstance(raw, dict):
        return None
    preset = BY_ID.get(str(raw.get("id") or ""))
    if preset is not None:
        return {"id": preset.id, "label": preset.label, "posture": preset.posture}
    label = str(raw.get("label") or "").strip()
    posture = str(raw.get("posture") or "").strip()
    if not label or not posture:
        return None
    return {"id": "custom", "label": label[:MAX_LABEL], "posture": posture[:MAX_POSTURE]}


def prompt_block(role: Optional[Dict[str, str]]) -> str:
    """The paragraph appended to the system prompt, or an empty string.

    It changes the *voice*, and says so: the role never authorises a tool, disables
    a skill, or redefines what a task is.
    """
    if not role:
        return ""
    label = str(role.get("label") or "").strip()
    posture = str(role.get("posture") or "").strip()
    if not label or not posture:
        return ""
    return (
        f" Character and role for this conversation: **{label}**. {posture} "
        "This shapes your posture, expertise and tone only: it never changes which tools, "
        "skills or MCP servers are available, and it never overrides an instruction that "
        "comes from the operator or a skill."
    )


def label_of(role: Optional[Dict[str, str]]) -> str:
    return str((role or {}).get("label") or "") or NONE_LABEL
