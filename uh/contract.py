"""Le contrat UH ↔ UHB, versionné (P2-9).

UH et UHB sont deux programmes séparés, livrés séparément. Jusqu'ici, UH supposait
la forme de ce que UHB publie : une réponse qui « ressemble » à un `/status` était
acceptée, quel que soit l'âge du binaire d'en face. Une version majeure différente
doit maintenant se voir et se dire, au lieu de produire des champs manquants loin
de leur cause.

Deux versions, et deux seulement :

* **majeure** — la forme des réponses. Une majeure différente n'est pas
  négociable : UH refuse d'appeler les outils de UHB et le dit en français.
* **mineure** — des champs ajoutés, rien de retiré. Un UHB plus récent est
  utilisable ; UH le signale comme « plus récent » sans rien bloquer.

Absence de version = UHB antérieur au contrat. On le traite comme la version 1
(« inconnu, présumé compatible ») et on le dit : refuser un UHB qui marche serait
une panne inventée.
"""

from __future__ import annotations

#: Ce que UH sait parler.
WIRE_NAME = "uh-uhb"
WIRE_VERSION = 1

STATE_OK = "ok"
STATE_UNKNOWN = "inconnu"
STATE_INCOMPATIBLE = "incompatible"


def parse(payload: dict) -> tuple[int, int] | None:
    """(majeure, mineure) publiées par UHB, ou ``None`` s'il n'en publie pas."""
    block = payload.get("contract")
    if not isinstance(block, dict):
        return None
    name = block.get("name")
    if name is not None and name != WIRE_NAME:
        # Un contrat qui porte un autre nom n'est pas le nôtre : incompatibilité.
        return None
    raw = block.get("version", block.get("major"))
    if isinstance(raw, str) and "." in raw:
        major, _, minor = raw.partition(".")
        try:
            return int(major), int(minor or 0)
        except ValueError:
            return None
    if isinstance(raw, int):
        raw_minor = block.get("minor")
        return raw, raw_minor if isinstance(raw_minor, int) else 0
    return None


def check(payload: dict) -> tuple[str, str]:
    """(état, phrase). Trois états, et la phrase dit quoi faire."""
    version = parse(payload)
    if version is None:
        if isinstance(payload.get("contract"), dict) and payload["contract"].get("name") not in (
            None,
            WIRE_NAME,
        ):
            return (
                STATE_INCOMPATIBLE,
                f"Second cerveau : contrat {payload['contract'].get('name')!r} au lieu de "
                f"{WIRE_NAME!r} — UH ne sait pas lui parler.",
            )
        return (
            STATE_UNKNOWN,
            f"Second cerveau antérieur au contrat : version non publiée, présumée "
            f"compatible (UH parle {WIRE_NAME} v{WIRE_VERSION}).",
        )
    major, minor = version
    if major != WIRE_VERSION:
        return (
            STATE_INCOMPATIBLE,
            f"Second cerveau en contrat v{major}, UH parle v{WIRE_VERSION} : majeure "
            "différente, les outils ne sont pas appelés. Mettez les deux à jour ensemble.",
        )
    if minor > 0:
        return (
            STATE_OK,
            f"Contrat {WIRE_NAME} v{major}.{minor} (UH parle v{WIRE_VERSION}.0) : compatible.",
        )
    return STATE_OK, f"Contrat {WIRE_NAME} v{major} : compatible."


def incompatible(payload: dict) -> bool:
    return check(payload)[0] == STATE_INCOMPATIBLE
