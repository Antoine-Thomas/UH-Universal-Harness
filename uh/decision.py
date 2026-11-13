"""The last turn's decision, as facts: complexity, model, chain, tokens, context.

The value of this view is that it can be wrong about nothing. UH does **not**
route every turn through UHB: the router runs when the model calls the
``uhb_route`` tool, or when the user asks (the Second brain tab). So a turn
usually has no ``complexity`` at all, and the honest answer is "UHB was not
consulted", not a plausible-looking "trivial".

Two layers, on purpose:

* ``record()`` writes what happened, at the end of a turn, into the session's
  JSON ``metadata`` (``decisions``, last ten). No schema migration: sessions
  already carry metadata, and a decision is per session, not per message.
* ``payload()`` reads it back and fills the gaps with an explanation. It never
  invents a field it cannot know.

The served model is ``provider.last_real_model`` - an alias can resolve to
another tag, and pretending the chosen model answered would make the whole
panel decorative.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

#: How many turns the panel keeps: enough for an average, small enough to stay
#: inside a metadata blob that rides on every session listing.
KEEP = 10
#: The average duration is taken over this many turns (the spec's "last five").
AVERAGE_WINDOW = 5

NOT_ROUTED = (
    "UHB was not consulted for this turn: no uhb_route call and no explicit "
    "routing. Ask UHB for a plan from this tab if you want to compare."
)


def _tier_of(candidate: object) -> str:
    if isinstance(candidate, dict):
        return str(candidate.get("tier") or "?")
    return "?"


def build_entry(
    *,
    provider: str,
    model: str,
    served: Optional[str],
    usage: Optional[dict],
    duration_ms: int,
    budget: Optional[int],
    summary: str = "",
    summarised_exchanges: int = 0,
    kept_exchanges: int = 0,
    context_mode: str = "none",
    payload_tokens: int = 0,
    route: Optional[dict] = None,
    tool_calls: int = 0,
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """One compact record of a turn. Everything here was measured."""
    counters = usage or {}
    reported = bool(usage)
    chain: List[dict] = []
    complexity = None
    mode = None
    reason = ""
    if route and "error" not in route:
        complexity = route.get("complexity")
        mode = route.get("mode")
        reason = str(route.get("reason") or "")
        for candidate in route.get("chain") or []:
            if isinstance(candidate, dict):
                chain.append({"ref": candidate.get("ref"), "tier": _tier_of(candidate)})
    return {
        "provider": provider,
        "model": model,
        "served": served or model,
        "substituted": bool(served and served != model),
        "usage": {
            "prompt": int(counters.get("prompt") or 0),
            "completion": int(counters.get("completion") or 0),
            "total": int(counters.get("total") or 0),
            "reported": reported,
        },
        "duration_ms": int(duration_ms),
        "budget": int(budget) if budget else None,
        "summary": summary or "",
        "summarised_exchanges": int(summarised_exchanges),
        "kept_exchanges": int(kept_exchanges),
        "context_mode": context_mode,
        "payload_tokens": int(payload_tokens),
        "complexity": complexity,
        "mode": mode,
        "reason": reason,
        "chain": chain,
        "chosen": (route or {}).get("chosen") if route else None,
        "tool_calls": int(tool_calls),
        # When, not just what: the telemetry panel buckets tokens per day, and a
        # turn with no date cannot be placed on one without guessing.
        "at": str(at) if at else datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def record(store: Any, session_id: Optional[str], entry: Dict[str, Any]) -> bool:
    """Append ``entry`` to the session's decisions. ``False`` when it cannot."""
    if not session_id:
        return False
    session = store.get_session(session_id)
    if session is None:
        return False
    metadata = dict(session.get("metadata") or {})
    decisions = list(metadata.get("decisions") or [])
    decisions.append(entry)
    metadata["decisions"] = decisions[-KEEP:]
    metadata["last_decision"] = entry
    return bool(store.set_session_metadata(session_id, metadata))


def _empty(reason: str, session_id: Optional[str]) -> Dict[str, Any]:
    return {
        "session_id": session_id,
        "available": False,
        "reason": reason,
        "turn": {"duration_ms": None, "average_ms": None, "turns": 0},
        "prompt": {"complexity": None, "mode": None, "reason": "", "source": "unavailable",
                   "note": NOT_ROUTED},
        "model": {"provider": None, "model": None, "served": None, "substituted": False},
        "chain": [],
        "usage": {"prompt": 0, "completion": 0, "total": 0, "reported": False},
        "context": {"budget": None, "summarised_exchanges": 0, "kept_exchanges": 0,
                    "summary": "", "mode": "none", "payload_tokens": 0},
        "uhb_consulted": False,
        "turns": [],
    }


def payload(store: Any, session_id: str) -> Dict[str, Any]:
    """The last recorded turn, with an average, or an explanation of its absence."""
    session = store.get_session(session_id)
    if session is None:
        return _empty("unknown session", session_id)
    metadata = dict(session.get("metadata") or {})
    decisions = [item for item in (metadata.get("decisions") or []) if isinstance(item, dict)]
    if not decisions:
        return _empty(
            "no turn recorded for this session yet: the panel fills itself after the first answer",
            session_id,
        )

    last = dict(decisions[-1])
    window = decisions[-AVERAGE_WINDOW:]
    durations = [int(item.get("duration_ms") or 0) for item in window]
    average = int(sum(durations) / len(durations)) if durations else None
    consulted = last.get("complexity") is not None or bool(last.get("chain"))
    return {
        "session_id": session_id,
        "available": True,
        "reason": "",
        "turn": {
            "duration_ms": int(last.get("duration_ms") or 0),
            "average_ms": average,
            "turns": len(window),
        },
        "prompt": {
            "complexity": last.get("complexity"),
            "mode": last.get("mode"),
            "reason": last.get("reason") or "",
            "source": "uhb" if consulted else "unavailable",
            "note": "" if consulted else NOT_ROUTED,
        },
        "model": {
            "provider": last.get("provider"),
            "model": last.get("model"),
            "served": last.get("served"),
            "substituted": bool(last.get("substituted")),
            "chosen": last.get("chosen"),
        },
        "chain": list(last.get("chain") or []),
        "usage": dict(last.get("usage") or {"prompt": 0, "completion": 0, "total": 0,
                                            "reported": False}),
        "context": {
            "budget": last.get("budget"),
            "summarised_exchanges": int(last.get("summarised_exchanges") or 0),
            "kept_exchanges": int(last.get("kept_exchanges") or 0),
            "summary": str(last.get("summary") or ""),
            "mode": str(last.get("context_mode") or "none"),
            "payload_tokens": int(last.get("payload_tokens") or 0),
        },
        "uhb_consulted": consulted,
        "tool_calls": int(last.get("tool_calls") or 0),
        "turns": [
            {"duration_ms": int(item.get("duration_ms") or 0),
             "provider": item.get("provider"),
             "model": item.get("model")}
            for item in reversed(window)
        ],
    }


__all__ = ["AVERAGE_WINDOW", "KEEP", "NOT_ROUTED", "build_entry", "payload", "record"]
