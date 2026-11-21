"""Read-only modes: what the model may be offered, and why a tool is missing.

Two rules, both from point 2 of the relay:

* **do not offer what the mode forbids.** A model that taps a closed door costs a
  provider call, an approval question nobody should be asked, and teaches the
  operator nothing. In a read-only mode the offer is the read-only view of the
  catalogue;
* **name the cause when a call happens anyway.** A model can invent a tool name or
  replay one from the history. That call must come back typed ``mode_forbidden``,
  with the mode that refused it, so the browser can show « Autoriser pour cette
  conversation » instead of an anonymous "unknown tool" — and it must never reach
  the approval gate: asking permission for something the mode forbids is a
  question with only one honest answer.

The mapping is deliberately narrow and explicit. ``READ_ONLY_TOOLS`` names the
tools that only read; everything else — ``write_file``, ``patch``, ``terminal``,
``memory_write``, ``execute_code``, the ``desktop_*`` family, every remote MCP
tool — is dropped, because a read-only mode cannot vouch for it. A tool added
later is therefore withheld by default: the list is an allow-list, not a ban-list.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

__all__ = [
    "READ_ONLY_MODES",
    "READ_ONLY_TOOLS",
    "ReadOnlyToolset",
    "is_read_only",
    "read_only_view",
]

#: Modes whose profile cannot write. ``chat`` is the one that shipped with the
#: product; ``review`` and ``observe`` keep their own definitions in UHB and are
#: not guessed here.
READ_ONLY_MODES = frozenset({"chat"})

#: UH tools that read and nothing else. Names match the schemas offered to the
#: model, which is what `read_only_view` filters on.
READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "search_files",
        "web_search",
        "memory_read",
        "uhb_status",
        "uhb_modes",
        "uhb_route",
        "describe_tools",
    }
)


def is_read_only(mode: str) -> bool:
    """True when ``mode`` names a profile that cannot write."""
    return mode.strip().lower() in READ_ONLY_MODES


def _tool_name(spec: Any) -> str:
    if not isinstance(spec, dict):
        return ""
    function = spec.get("function")
    return str(function.get("name") or "") if isinstance(function, dict) else ""


def offered_names(toolsets: Dict[str, Any]) -> List[str]:
    """Every tool name the model is being offered, across the given toolsets."""
    return sorted(
        _tool_name(spec)
        for toolset in toolsets.values()
        for spec in (getattr(toolset, "tools", None) or [])
        if _tool_name(spec)
    )


def mode_refused_frame(tool: str, content: Any) -> dict | None:
    """The SSE frame the browser needs when a tool result says the *mode* refused.

    ``None`` means "not a mode refusal", so the turn shows the ordinary tool card.
    The browser keys its « Autoriser pour cette conversation » card on this frame:
    one frame per refusal, carrying the mode that refused, so the button can name
    what it will switch to.
    """
    text = str(content or "")
    if "mode_forbidden" not in text:
        return None
    mode = ""
    try:
        detail = json.loads(text)
    except (TypeError, ValueError):
        detail = None
    if isinstance(detail, dict):
        mode = str(detail.get("mode") or "")
    return {"type": "mode_refused", "tool": str(tool or ""), "mode": mode or "chat"}


class ReadOnlyToolset:
    """A toolset wearing a read-only view: the same object, fewer schemas.

    A proxy rather than a mutation: the underlying toolset is shared with the rest
    of the process (and the next turn), and trimming its ``tools`` list in place
    would leave a read-only mode leaking into the next turn.
    """

    def __init__(
        self, toolset: Any, mode: str = "", allowed: frozenset[str] = READ_ONLY_TOOLS
    ) -> None:
        self._toolset = toolset
        self._mode = mode
        self.name = str(getattr(toolset, "name", "") or "")
        offered = [spec for spec in (getattr(toolset, "tools", None) or [])]
        self.tools: List[dict] = [spec for spec in offered if _tool_name(spec) in allowed]
        #: What this view withheld, so a call to one of them can name its cause
        #: instead of reading as "unknown tool" (point 2).
        self._withheld = {_tool_name(spec) for spec in offered} - allowed

    def call(self, name: str, args: Dict[str, Any]) -> dict:
        """Delegate: the read-only view only decides what is *offered*.

        A call that reaches here was offered in this turn, so it is a read; the
        toolset remains the only one that knows how to answer it. The result is
        normalised to a dict here so the proxy keeps the toolset contract.
        """
        result = self._toolset.call(name, args)
        return result if isinstance(result, dict) else {"result": result}

    def explain_missing(self, name: str) -> dict | None:
        """Why a tool is missing: withheld here, or explained by the toolset."""
        if name in self._withheld:
            return {
                "error": (
                    f"`{name}` n'est pas proposé dans un mode en lecture seule "
                    f"(mode « {self._mode or 'courant'} »). Le changement de mode "
                    "redémarre UHB : utilise la carte « Autoriser pour cette conversation »."
                ),
                "type": "forbidden",
                "tool": name,
                "reason": "mode_forbidden",
                "mode": self._mode,
            }
        explain = getattr(self._toolset, "explain_missing", None)
        return explain(name) if callable(explain) else None


def read_only_view(toolsets: Dict[str, Any], mode: str = "") -> Tuple[Dict[str, Any], List[str]]:
    """The read-only subset of ``toolsets``, and the tools it withheld.

    A toolset whose tools are all withheld is dropped: an empty toolset is still a
    name in the offer, and `run_turn` counts it when it looks for something to run.
    """
    kept: Dict[str, Any] = {}
    withheld: List[str] = []
    for key, toolset in toolsets.items():
        view = ReadOnlyToolset(toolset, mode)
        offered = {_tool_name(spec) for spec in (getattr(toolset, "tools", None) or [])}
        withheld.extend(sorted(offered - READ_ONLY_TOOLS))
        if view.tools:
            kept[key] = view
    return kept, withheld
