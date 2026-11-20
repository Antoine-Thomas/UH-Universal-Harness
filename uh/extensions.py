"""Le gestionnaire d'extensions : ce qui est branché, ce qui est actif, et pourquoi pas.

Trois familles derrière une seule vue : les serveurs **MCP**, les **skills**, et le
**catalogue d'outils du second cerveau** (UHB). Chaque famille garde son propriétaire -
`uh/mcp.py` sait lancer un serveur, `uh/skills.py` sait lire un fichier, `uh/skill_prefs.py`
retient les skills éteints - et ce module ne fait que trois choses : rassembler, décider de
l'état, éprouver.

Règles, chacune pour une raison :

* **des identifiants, jamais des chemins** : un nom d'extension ne devient jamais un chemin
  ni une ligne de commande ;
* **écrire est atomique** (fichier temporaire + ``os.replace``) : un arrêt au milieu ne
  doit pas perdre les choix de l'opérateur ;
* **un registre corrompu vaut « tout actif »** : UH doit démarrer même avec un JSON cassé ;
* **l'effet est à chaud** : la liste d'outils est construite à chaque tour, donc éteindre
  une extension se voit au tour suivant sans redémarrer `uh web`. C'est écrit ici parce que
  c'est la promesse la plus facile à casser sans s'en apercevoir.

La règle d'honnêteté de la vue : une extension éteinte reste **listée** (sinon on ne
pourrait plus la rallumer), et une extension cassée dit **pourquoi** - config invalide,
handshake échoué, binaire absent.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config import uh_home

FILE_NAME = "extensions.json"
#: Les familles que cette vue rassemble. `skills` délègue ses préférences à
#: :mod:`uh.skill_prefs` : deux registres pour la même décision divergeraient.
KINDS = ("mcp", "skills", "uhb")
#: Borné : ce fichier est une liste de noms, pas un endroit où coller des données.
MAX_ENTRIES = 500


class ExtensionsError(ValueError):
    """La demande est refusée ; le message dit laquelle et pourquoi. Rien n'a changé."""


# --------------------------------------------------------------------------- #
# le registre (exceptions : ce qui est éteint)
# --------------------------------------------------------------------------- #
def registry_path(home: Optional[Path] = None) -> Path:
    return (home or uh_home()) / FILE_NAME


def _empty() -> Dict[str, List[str]]:
    return {"mcp": [], "uhb": []}


def _read(home: Optional[Path] = None) -> Dict[str, List[str]]:
    path = registry_path(home)
    if not path.is_file():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Un JSON cassé ne doit pas empêcher UH de démarrer : tout redevient actif, et le
        # fichier reste sur disque pour que l'opérateur puisse le lire.
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    disabled = raw.get("disabled")
    if not isinstance(disabled, dict):
        return _empty()
    answer = _empty()
    for kind in answer:
        names = disabled.get(kind)
        if isinstance(names, list):
            answer[kind] = [str(name) for name in names if isinstance(name, str)][:MAX_ENTRIES]
    return answer


def _write(data: Dict[str, List[str]], home: Optional[Path] = None) -> None:
    path = registry_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"disabled": data}, ensure_ascii=False, indent=2) + "\n"
        handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".extensions-", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temp, path)
    except OSError as exc:
        raise ExtensionsError(f"préférences non enregistrées : {exc}") from exc


def disabled(kind: str, home: Optional[Path] = None) -> Set[str]:
    """Les noms éteints d'une famille. Un genre inconnu n'a rien d'éteint : il n'existe pas."""
    if kind == "skills":
        from . import skill_prefs

        return set(skill_prefs.disabled(home))
    if kind not in KINDS:
        raise ExtensionsError(f"famille inconnue : {kind!r} (attendu : {', '.join(KINDS)})")
    return set(_read(home).get(kind) or [])


def is_enabled(kind: str, name: str, home: Optional[Path] = None) -> bool:
    """Une extension est-elle active ? Les skills se comparent par identifiant ``origin:nom``."""
    if kind == "skills":
        from . import skill_prefs

        wanted = str(name)
        for identifier in skill_prefs.disabled(home):
            if str(identifier).split(":", 1)[-1] == wanted:
                return False
        return True
    return str(name) not in disabled(kind, home)


def set_enabled(kind: str, name: str, enabled: bool, home: Optional[Path] = None) -> Dict[str, Any]:
    """Allume ou éteint une extension, après avoir vérifié qu'elle existe vraiment."""
    label = str(name or "").strip()
    if not label:
        raise ExtensionsError("un nom d'extension est obligatoire")
    if kind not in KINDS:
        raise ExtensionsError(f"famille inconnue : {kind!r} (attendu : {', '.join(KINDS)})")
    known = {entry["name"]: entry for entry in inventory(home)["groups"][kind]}
    if label not in known:
        raise ExtensionsError(f"extension inconnue : {label!r} (famille {kind})")

    if kind == "skills":
        from . import skill_prefs

        origin = str(known[label].get("origin") or "user")
        result = skill_prefs.set_enabled(label, origin, bool(enabled), home)
        return {"kind": kind, "name": label, "enabled": bool(enabled), "prefs": result}

    data = _read(home)
    names = set(data.get(kind) or [])
    if enabled:
        names.discard(label)
    else:
        names.add(label)
    data[kind] = sorted(names)[:MAX_ENTRIES]
    _write(data, home)
    return {"kind": kind, "name": label, "enabled": bool(enabled)}


# --------------------------------------------------------------------------- #
# l'inventaire
# --------------------------------------------------------------------------- #
def _mcp_entries(home: Optional[Path]) -> List[Dict[str, Any]]:
    from . import mcp

    off = set(_read(home).get("mcp") or [])
    entries: List[Dict[str, Any]] = []
    try:
        report = mcp.status()
    except Exception as exc:  # noqa: BLE001 - une config illisible est un diagnostic
        return [
            {
                "kind": "mcp",
                "name": "(configuration)",
                "state": "broken",
                "enabled": False,
                "detail": "la configuration MCP n'a pas pu être lue",
                "reason": f"{type(exc).__name__}: {exc}",
                "tools": [],
            }
        ]
    for server in report.get("servers") or []:
        name = str(server.get("name") or "")
        if not name:
            continue
        state = str(server.get("state") or "idle")
        entries.append(
            {
                "kind": "mcp",
                "name": name,
                "state": state,
                "enabled": name not in off,
                "detail": f"{server.get('command') or '?'} · {len(server.get('tools') or [])} outil(s)",
                "reason": str(server.get("reason") or ""),
                "tools": [str(tool) for tool in server.get("tools") or []],
            }
        )
    for problem in report.get("problems") or []:
        entries.append(
            {
                "kind": "mcp",
                "name": str(problem.get("name") or "(entrée refusée)"),
                "state": "broken",
                "enabled": False,
                "detail": "configuration refusée avant tout démarrage",
                "reason": str(problem.get("reason") or problem),
                "tools": [],
            }
        )
    return entries


def _skill_entries(home: Optional[Path]) -> List[Dict[str, Any]]:
    from . import skill_prefs, skills

    try:
        listed = skill_prefs.for_listing(skills.list_skills(home).get("skills") or [], home)
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "kind": "skills",
                "name": "(dossier)",
                "state": "broken",
                "enabled": False,
                "detail": "les skills n'ont pas pu être lus",
                "reason": f"{type(exc).__name__}: {exc}",
                "tools": [],
            }
        ]
    entries: List[Dict[str, Any]] = []
    for skill in listed:
        name = str(skill.get("name") or "")
        if not name:
            continue
        entries.append(
            {
                "kind": "skills",
                "name": name,
                "state": "ready",
                "enabled": bool(skill.get("enabled", True)),
                "detail": f"{skill.get('origin') or '?'} · {skill.get('description') or 'sans description'}",
                "reason": "",
                "origin": skill.get("origin") or "user",
                "tools": [],
            }
        )
    return entries


def _uhb_entries(home: Optional[Path]) -> List[Dict[str, Any]]:
    """Le catalogue d'outils du second cerveau, outil par outil."""
    from .toolsets.uhb import TOOLS, resolve_url

    off = set(_read(home).get("uhb") or [])
    url = resolve_url()
    reachable = bool(url)
    entries: List[Dict[str, Any]] = []
    for spec in TOOLS:
        name = str(spec.get("function", {}).get("name") or "")
        if not name:
            continue
        entries.append(
            {
                "kind": "uhb",
                "name": name,
                "state": "ready" if reachable else "unreachable",
                "enabled": name not in off,
                "detail": f"second cerveau {url or 'non configuré'} · {spec.get('function', {}).get('description', '')[:80]}",
                "reason": "" if reachable else "UH_UHB_URL vide ou hôte non loopback : l'outil est proposé mais échouera",
                "tools": [],
            }
        )
    return entries


def inventory(home: Optional[Path] = None) -> Dict[str, Any]:
    """Les trois familles, avec leur état et la raison quand ça ne va pas."""
    groups: Dict[str, List[Dict[str, Any]]] = {
        "mcp": _mcp_entries(home),
        "skills": _skill_entries(home),
        "uhb": _uhb_entries(home),
    }
    counts = {
        kind: {
            "total": len(entries),
            # Une extension cassée n'est pas « active » : elle est listée, elle a une
            # raison, mais elle ne compte pas dans ce que le modèle reçoit.
            "enabled": sum(
                1
                for entry in entries
                if entry.get("enabled") and entry.get("state") not in {"broken", "failed"}
            ),
            "broken": sum(1 for entry in entries if entry.get("state") in {"broken", "failed"}),
        }
        for kind, entries in groups.items()
    }
    return {"groups": groups, "counts": counts, "kinds": list(KINDS)}


# --------------------------------------------------------------------------- #
# l'épreuve
# --------------------------------------------------------------------------- #
def test(kind: str, name: str, home: Optional[Path] = None) -> Dict[str, Any]:
    """Éprouve UNE extension maintenant, et rend ce qui s'est passé.

    Tester une extension éteinte est permis : c'est justement à ça qu'on veut savoir si on
    peut la rallumer. Aucune épreuve ne modifie l'état du registre.
    """
    label = str(name or "").strip()
    if kind not in KINDS:
        raise ExtensionsError(f"famille inconnue : {kind!r}")
    known = {entry["name"]: entry for entry in inventory(home)["groups"][kind]}
    if label not in known:
        raise ExtensionsError(f"extension inconnue : {label!r} (famille {kind})")

    if kind == "mcp":
        from . import mcp
        from . import mcp_http as uh_mcp_http

        try:
            if any(server.name == label for server in uh_mcp_http.servers_from_config()):
                report = uh_mcp_http.test_connection(label)
            else:
                report = mcp.test_server(label)
        except Exception as exc:  # noqa: BLE001 - l'échec EST la réponse
            return {
                "kind": kind,
                "name": label,
                "ok": False,
                "detail": _explain(type(exc).__name__, str(exc)),
            }
        tools = report.get("tools") or []
        return {
            "kind": kind,
            "name": label,
            "ok": bool(report.get("ok", True)),
            "detail": f"{len(tools)} outil(s) listé(s)" if tools else "le serveur a répondu",
        }

    if kind == "skills":
        from . import skills

        entry = skills.read_skill(label, home)
        if entry is None:
            return {"kind": kind, "name": label, "ok": False, "detail": "fichier illisible ou introuvable"}
        instructions = str(entry.get("instructions") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not instructions:
            return {"kind": kind, "name": label, "ok": False, "detail": "le skill n'a aucune instruction à suivre"}
        return {
            "kind": kind,
            "name": label,
            "ok": True,
            "detail": f"« {title or label} » · {len(instructions)} caractères d'instructions",
        }

    from .toolsets.uhb import Uhbtoolset

    toolset = Uhbtoolset.from_environment()
    if toolset is None:
        return {
            "kind": kind,
            "name": label,
            "ok": False,
            "detail": "second cerveau non configuré (UH_UHB_URL vide ou hôte non loopback)",
        }
    try:
        status = toolset.status()
    except Exception as exc:  # noqa: BLE001
        return {"kind": kind, "name": label, "ok": False, "detail": _explain(type(exc).__name__, str(exc))}
    connected = bool(status.get("connected"))
    return {
        "kind": kind,
        "name": label,
        "ok": connected,
        "detail": f"mode {status.get('mode') or '?'} · v{status.get('version') or '?'}"
        if connected
        else "le second cerveau ne répond pas",
    }


def _explain(kind: str, message: str) -> str:
    """Traduire une panne en cause probable : « binaire absent » se corrige, « erreur » non.

    Le texte est examiné AVANT le type d'exception : un binaire absent arrive emballé dans
    un ``MCPError`` (« cannot start 'x' ([WinError 2] Le fichier spécifié est introuvable) »),
    et le classer comme « refus de dialogue » enverrait l'opérateur au mauvais endroit.
    """
    text = " ".join((message or "").split())
    lowered = text.lower()
    if (
        kind in {"FileNotFoundError", "NotADirectoryError"}
        or "no such file" in lowered
        or "introuvable" in lowered
        or "cannot start" in lowered
        or "winerror 2" in lowered
    ):
        return f"binaire introuvable : {text}"
    if kind == "TimeoutError" or "timed out" in lowered:
        return f"handshake échoué (délai dépassé) : {text}"
    if kind in {"MCPError", "MCPHTTPError"}:
        return f"le serveur a refusé le dialogue : {text}"
    return f"{kind} : {text}"


def enabled_names(kind: str, home: Optional[Path] = None) -> Set[str]:
    """Ce que le modèle peut recevoir : ce qui est allumé et qui n'est pas cassé.

    On lit l'état dans l'inventaire plutôt que dans le registre : les skills portent un
    identifiant ``origin:nom`` et seul `for_listing` sait le traduire.
    """
    return {
        str(entry["name"])
        for entry in inventory(home)["groups"][kind]
        if entry.get("enabled") and entry.get("state") not in {"broken", "failed"}
    }


__all__ = [
    "FILE_NAME",
    "KINDS",
    "MAX_ENTRIES",
    "ExtensionsError",
    "disabled",
    "enabled_names",
    "inventory",
    "is_enabled",
    "registry_path",
    "set_enabled",
    "test",
]
