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
from typing import Dict, List

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


def build_toolsets(workspace: str | os.PathLike[str] | None = None) -> Dict[str, object]:
    """Build the Jalon 1 toolset set, keyed by toolset name.

    ``uhb`` joins only when a UHB control plane answers on its loopback URL
    (1 s probe): a dead or older UHB contributes no tools rather than a tool
    that fails on every call. ``UH_UHB_URL=""`` disables it explicitly.

    ``mcp:<server>`` joins for every ``[[mcp.servers]]`` entry that completes the
    MCP handshake (see :mod:`uh.mcp`). The processes are cached per entry, so a
    turn after the first costs no spawn; a server that failed is not retried for
    :data:`uh.mcp.RETRY_AFTER` seconds, and contributes no tool meanwhile.
    """
    ws = Path(workspace) if workspace is not None else None
    toolsets: Dict[str, object] = {
        "file": FileToolset(workspace=ws),
        "terminal": TerminalToolset(workspace=ws),
        "memory": MemoryToolset(),
        "web_search": WebSearchToolset(),
    }
    uhb = Uhbtoolset.from_environment()
    if uhb is not None:
        toolsets["uhb"] = uhb
    try:
        from .. import mcp

        toolsets.update(mcp.build_toolsets())
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
