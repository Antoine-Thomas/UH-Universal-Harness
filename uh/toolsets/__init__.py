"""Toolsets available to the agent loop (Jalon 1).

Every toolset exposes the same three members, so ``run_turn`` only needs duck
typing:

    name: str                  # toolset name, used in reports and /tools
    tools: list[dict]          # OpenAI/Ollama JSON schemas for the provider
    call(name, args) -> dict   # result payload, or {"error": "..."}

A toolset never raises out of ``call`` for a user-level mistake (bad path,
blocked command, unknown tool): it returns ``{"error": ...}`` which the agent
loop turns into a ``role: tool`` message. Genuine bugs still propagate and are
caught by the loop as well.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, cast

from .file import FileToolset
from .memory import MemoryToolset
from .terminal import TerminalToolset
from .uhb import Uhbtoolset
from .web_search import WebSearchToolset

__all__ = [
    "FileToolset",
    "MemoryToolset",
    "TerminalToolset",
    "Uhbtoolset",
    "WebSearchToolset",
    "build_toolsets",
    "describe_toolsets",
]


def _protected_by_default() -> list[Path]:
    """Racines protegees : le dossier de configuration de UH, en entier.

    Il n'est lisible que par une `read_root` explicite : le workspace ne suffit jamais, meme
    quand `~/.uh` se trouve dedans.

    Fail-closed : si la racine ne peut pas etre determinee, **la construction leve** et aucun
    toolset n'est produit. Un `except: return []` desactiverait silencieusement la protection,
    ce qui est exactement le defaut qu'on vient de fermer. Aucune exception n'est attendue ici en
    usage normal ; celle qui survient doit etre vue, pas absorbee.

    `uh_home()` est importe depuis `.memory` (meme paquet) plutot que via `attachments` : cela
    evite un cycle a l'import, sans recourir a un `try`.
    """
    from .memory import uh_home

    return [uh_home()]


def protected_roots() -> list[Path]:
    """Les racines protegees, pour ceux qui construisent un `FileToolset` a la main.

    Le web s'en sert pour son explorateur : il doit refuser exactement ce que refusent
    les outils, sans recopier la liste (une liste recopiee diverge au premier ajout).
    """
    return _protected_by_default()


def build_toolsets(
    workspace: str | os.PathLike[str] | None = None,
    notify: Any = None,
    read_roots: list[Path] | None = None,
    protected_roots: list[Path] | None = None,
) -> Dict[str, object]:
    """Build the Jalon 1 toolset set, keyed by toolset name.

    ``uhb`` joins only when a UHB control plane answers on its loopback URL
    (1 s probe): a dead or older UHB contributes no tools rather than a tool
    that fails on every call. ``UH_UHB_URL=""`` disables it explicitly.
    ``notify`` is the turn's stream, threaded down so `execute_code` can ask the
    operator before it runs: without it the tool is advertised and refuses.

    ``mcp:<server>`` joins for every ``[[mcp.servers]]`` entry that completes the
    MCP handshake (see :mod:`uh.mcp`). The processes are cached per entry, so a
    turn after the first costs no spawn; a server that failed is not retried for
    :data:`uh.mcp.RETRY_AFTER` seconds, and contributes no tool meanwhile.
    """
    ws = Path(workspace) if workspace is not None else None
    toolsets: Dict[str, object] = {
        "file": FileToolset(
            workspace=ws,
            read_roots=read_roots,
            protected_roots=(
                protected_roots if protected_roots is not None else _protected_by_default()
            ),
        ),
        "terminal": TerminalToolset(workspace=ws),
        "memory": MemoryToolset(),
        "web_search": WebSearchToolset(),
    }
    uhb = Uhbtoolset.from_environment(notify=notify)
    if uhb is not None:
        toolsets["uhb"] = uhb
    try:
        from .. import extensions as uh_extensions
        from .. import mcp

        # Le gestionnaire d'extensions décide AVANT le travail : un serveur éteint n'est
        # même pas lancé, et un outil UHB éteint n'est pas proposé au modèle. Ces deux
        # lectures se font à chaque construction (donc à chaque tour), ce qui rend l'effet
        # immédiat sans redémarrer le serveur web.
        try:
            toolsets.update(mcp.build_toolsets(skip=uh_extensions.disabled("mcp")))
        except TypeError:
            # Un `mcp.build_toolsets` remplacé (test, module antérieur) qui ne connaît pas
            # encore `skip` : on garde ses outils plutôt que de les perdre en silence. Un
            # `except Exception` plus large a déjà caché cette régression une fois.
            toolsets.update(mcp.build_toolsets())
        off_uhb = uh_extensions.disabled("uhb")
        if "uhb" in toolsets and off_uhb:
            # `cast(Any)` plutôt que `setattr(...)` : la règle B010 de ruff réécrit un
            # `setattr(x, "constant", v)` en `x.constant = v`, ce qui remet l'erreur mypy
            # (le dictionnaire est typé `object`) - la correction se défaisait à chaque
            # passage de `ruff --fix`.
            toolset = cast(Any, toolsets["uhb"])
            current = getattr(toolset, "tools", None) or []
            kept = [
                spec
                for spec in current
                if str(spec.get("function", {}).get("name") or "") not in off_uhb
            ]
            if not kept:
                toolsets.pop("uhb", None)
            else:
                toolset.tools = kept
    except Exception:  # noqa: BLE001 - an external server never breaks the built-ins
        pass
    return toolsets


def describe_toolsets(toolsets: Dict[str, object]) -> List[str]:
    """Human-readable ``toolset: tool, tool`` lines, for ``uh chat /tools``."""
    lines: List[str] = []
    for name, toolset in toolsets.items():
        tool_names = [
            spec.get("function", {}).get("name")
            for spec in getattr(toolset, "tools", None) or []
            if isinstance(spec, dict)
        ]
        lines.append(f"{name}: " + ", ".join(n for n in tool_names if n))
    return lines
