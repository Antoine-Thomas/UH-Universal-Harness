"""Politique de voix cloud : demander (défaut), jamais, toujours.

Un « Écouter » ne doit pas envoyer le texte du fil à Mistral parce qu'une clé
traîne dans l'environnement. Mesure de départ (27/11) : `speak()` essayait Voxtral
**en premier** dès que la clé existait, sans que l'interface le dise — et
`--local-only`, qui ne gouverne que les fournisseurs de modèle, ne l'arrêtait pas
(d'où G17).

Trois valeurs, dans `~/.uh/voice.toml` :

* ``ask`` (défaut) — rien ne part sans un accord explicite de l'appelant ;
* ``never`` — rien ne part, même avec accord ;
* ``always`` — l'accord est donné d'avance par un réglage visible.
"""

from __future__ import annotations

import os
from pathlib import Path

POLICY_FILE = "voice.toml"
DEFAULT_POLICY = "ask"
POLICIES = ("ask", "never", "always")

#: Ce que l'interface affiche **avant** un appel cloud.
CONSENT_LINE = "Le texte sera envoyé à Mistral"

#: Quand aucun moteur serveur ne répond, la décision est prise une fois ici : c'est
#: la voix du navigateur qui lit, sans calcul local, et l'interface le dit.
FALLBACK_ENGINE = "browser"
FALLBACK_INFO = (
    "Repli : la voix du navigateur lit le texte à voix haute — aucun calcul local, "
    "aucun envoi à Mistral"
)

_calls = 0


def policy_path() -> Path:
    from ..config import uh_home  # local import: `uh_home` reads the env at call time

    return uh_home() / POLICY_FILE


def load_policy() -> str:
    """La politique enregistrée, ou ``ask`` si le fichier est absent ou illisible."""
    path = policy_path()
    if not path.is_file():
        return DEFAULT_POLICY
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "cloud":
                value = value.strip().strip('"').strip("'").lower()
                return value if value in POLICIES else DEFAULT_POLICY
    except OSError:
        return DEFAULT_POLICY
    return DEFAULT_POLICY


def save_policy(value: str) -> Path:
    """Écrit la politique (``ask``, ``never`` ou ``always``) et rend le fichier."""
    chosen = str(value or "").strip().lower()
    if chosen not in POLICIES:
        raise ValueError(f"politique inconnue : {value!r} (attendu : {', '.join(POLICIES)})")
    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(f'cloud = "{chosen}"\n', encoding="utf-8")
    os.replace(tmp, path)
    return path


def cloud_allowed(policy: str, consented: bool | None) -> bool:
    """L'appel cloud est-il permis, pour cette politique et ce consentement ?"""
    if policy == "never":
        return False
    if policy == "always":
        return True
    return consented is True


def refusal_reason(policy: str) -> str:
    """Pourquoi Voxtral n'a pas été appelé, en une phrase qui ne fuit aucun secret."""
    if policy == "never":
        return "politique « jamais » : aucun texte ne part chez Mistral"
    return "accord non donné (politique « demander ») : ajoutez le consentement pour la voix cloud"


def note_cloud_call() -> int:
    """Compte un appel cloud réellement tenté, et rend le total."""
    global _calls
    _calls += 1
    return _calls


def cloud_calls() -> int:
    return _calls


def reset_cloud_calls() -> None:
    global _calls
    _calls = 0
