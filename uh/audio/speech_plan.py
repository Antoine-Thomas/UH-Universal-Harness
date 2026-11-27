"""Découper un texte long en morceaux dictables, sans jamais en perdre.

Mesures du 27/11 : XTTS met ~35 ms par caractère et pic à ≈ 4,2 Go de VRAM. Un
texte de 5 000 caractères n'est donc pas un tour de parole, c'est cinq minutes de
calcul : il doit être lu par morceaux, et l'interface doit dire combien.

Règle de base : **aucun texte n'est tronqué en silence**. Un texte qui demanderait
plus de morceaux que le maximum est refusé, avec le compte et la limite ; une
phrase plus longue qu'un morceau est coupée sur les espaces (jamais au milieu d'un
mot quand c'est évitable), ce qui ne perd rien.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import AudioUnavailable

#: Ce qu'un moteur autorgressif digère d'un coup sans tenir la VRAM trop longtemps.
MAX_PART_CHARS = 2000
#: 12 x 2 000 = 24 000 caractères ≈ 14 minutes de calcul : au-delà, ce n'est plus
#: une réponse, c'est un livre — l'API refuse et le dit.
MAX_PARTS = 12

#: Une phrase se termine sur une ponctuation forte suivie d'un espace (ou de la fin).
_SENTENCE_END = re.compile(r"(?<=[.!?…;:])\s+")


@dataclass(frozen=True)
class SpeechPlan:
    """Les morceaux à lire, dans l'ordre, et de quoi le dire à l'interface."""

    parts: tuple[str, ...]
    total_chars: int
    limit: int

    @property
    def truncated(self) -> bool:
        """Toujours faux : on refuse un texte trop long plutôt que d'en couper la fin."""
        return False

    def as_dict(self) -> dict:
        return {
            "parts": list(self.parts),
            "count": len(self.parts),
            "total_chars": self.total_chars,
            "part_limit": self.limit,
            "truncated": self.truncated,
        }


def _split_long(sentence: str, limit: int) -> list[str]:
    """Une phrase plus longue qu'un morceau : on coupe sur les espaces.

    Un « mot » de plus de ``limit`` caractères (une URL, un identifiant) est coupé
    net : perdre la coupe coûterait plus que couper le mot, et rien n'est perdu.
    """
    pieces: list[str] = []
    current = ""
    for word in sentence.split(" "):
        while len(word) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(word[:limit])
            word = word[limit:]
        if not word:
            continue
        candidate = f"{current} {word}" if current else word
        if len(candidate) > limit:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def plan_speech(
    text: str, *, limit: int = MAX_PART_CHARS, max_parts: int = MAX_PARTS
) -> SpeechPlan:
    """Découpe ``text`` en morceaux de ``limit`` caractères au plus.

    Lève ``AudioUnavailable`` si le texte demanderait plus de ``max_parts``
    morceaux : mieux vaut un refus nommé qu'une narration amputée.
    """
    body = str(text or "").strip()
    if not body:
        raise AudioUnavailable("rien à lire : le texte est vide")
    if limit < 1 or max_parts < 1:
        raise ValueError("limit et max_parts doivent être positifs")

    sentences: list[str] = []
    for block in body.split("\n"):
        if block.strip():
            sentences.extend(piece.strip() for piece in _SENTENCE_END.split(block) if piece.strip())

    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(_split_long(sentence, limit))
            continue
        candidate = f"{current} {sentence}" if current else sentence
        if len(candidate) > limit:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)

    if len(parts) > max_parts:
        raise AudioUnavailable(
            f"texte trop long : {len(parts)} morceaux de {limit} caractères au plus, "
            f"maximum {max_parts} — lisez-le en plusieurs fois ou découpez-le"
        )
    return SpeechPlan(parts=tuple(parts), total_chars=len(body), limit=limit)
