"""Two memory actions UH can perform, by asking UHB and repeating its answer.

The cockpit observes; these two routes are the first that *act*. Both are
proxies on purpose: the browser never speaks to UHB (the CSP is `'self'`), and
UH is the only process that knows the loopback URL.

The mapping is the whole point of this module, and it is deliberately verbatim:

* UHB says 409 because the RAG service is down → UH says 409 with the command to
  start it. Rewriting that into "something went wrong" would throw away the only
  useful sentence UHB produced;
* UHB says 400 because `SIYUAN_TOKEN` is missing → UH says 400 and repeats the
  variable name;
* UHB cannot be reached at all → UH says 502 `uhb_offline`, which is a different
  failure from "SiYuan is down": one is our own backend, the other is its.

No action here rebuilds an index. `reload` asks the RAG service to re-read what
it already has; the wording says reloaded, never reindexed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

import httpx

#: A reload re-reads the index from disk: slower than a status probe, faster
#: than anything a user would wait for in a panel.
RELOAD_TIMEOUT = 5.0
#: A block listing is a query against a local kernel.
BLOCKS_TIMEOUT = 3.0
MAX_BLOCKS = 200
DEFAULT_BLOCKS = 20

#: Tests replace this with a transport-backed builder; production leaves it None.
_client_override: Optional[Callable[[float], httpx.Client]] = None


def set_client_factory(factory: Optional[Callable[[float], httpx.Client]]) -> None:
    """Swap the HTTP client builder. ``None`` restores the real one."""
    global _client_override
    _client_override = factory


def _client(timeout: float) -> httpx.Client:
    builder = _client_override
    return builder(timeout) if builder is not None else httpx.Client(timeout=timeout)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _detail(response: httpx.Response) -> str:
    """UHB's own sentence, when it sent one."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or "")
        if isinstance(error, str):
            return error
    return ""


def rag_reload(url: str, timeout: float = RELOAD_TIMEOUT) -> dict:
    """Ask UHB to reload the RAG index. Returns the body to send, plus a status."""
    target = f"{url.rstrip('/')}/rag/reindex"
    try:
        with _client(timeout) as http:
            response = http.post(target, json={})
    except Exception as exc:  # noqa: BLE001 - transport failure is an answer
        return {
            "status": 502,
            "payload": {
                "error": "UHB unreachable",
                "hint": f"start it with `uh web`, or check UH_UHB_URL ({type(exc).__name__})",
                "kind": "uhb_offline",
            },
        }
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        service = body.get("service") if isinstance(body, dict) else {}
        return {
            "status": 200,
            "payload": {
                "reloaded": bool(body.get("reloaded")) if isinstance(body, dict) else False,
                "service": service if isinstance(service, dict) else {},
                "at": _now(),
            },
        }
    if response.status_code == 409:
        detail = _detail(response)
        return {
            "status": 409,
            "payload": {
                "error": detail or "the RAG service is not running",
                "hint": "start it with: python uhb/services/rag/serveur_rag.py",
                "kind": "service_offline",
            },
        }
    return {
        "status": 502,
        "payload": {
            "error": _detail(response) or f"UHB answered HTTP {response.status_code}",
            "kind": "uhb_error",
        },
    }


def rag_status(url: str, timeout: float = BLOCKS_TIMEOUT) -> dict:
    """Ask UHB for the RAG state. The service's count beats our disk scan.

    UH reads the checkout folder; UHB talks to the service that actually holds
    the index. Reporting "not built" while the service answers 4893 fragments was
    a contradiction the panel showed for real, so the proxy wins when it answers.
    """
    target = f"{url.rstrip('/')}/rag/status"
    try:
        with _client(timeout) as http:
            response = http.get(target)
    except Exception:  # noqa: BLE001 - a dead UHB is not an error here
        return {"status": 502, "payload": {"kind": "uhb_offline"}}
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        return {"status": 200, "payload": body if isinstance(body, dict) else {}}
    return {"status": 502, "payload": {"kind": "uhb_error"}}


def siyuan_blocks(
    url: str, path: str = "", limit: int = DEFAULT_BLOCKS, timeout: float = BLOCKS_TIMEOUT
) -> dict:
    """Ask UHB for SiYuan blocks. Returns the body to send, plus a status."""
    wanted = max(1, min(int(limit or DEFAULT_BLOCKS), MAX_BLOCKS))
    target = f"{url.rstrip('/')}/siyuan/blocks"
    params = {"limit": str(wanted)}
    if path.strip():
        params["path"] = path.strip()
    try:
        with _client(timeout) as http:
            response = http.get(target, params=params)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": 502,
            "payload": {
                "error": "UHB unreachable",
                "hint": f"start it with `uh web`, or check UH_UHB_URL ({type(exc).__name__})",
                "kind": "uhb_offline",
            },
        }
    if response.status_code == 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        blocks = body.get("blocks") if isinstance(body, dict) else []
        return {
            "status": 200,
            "payload": {
                "count": int(body.get("count") or 0) if isinstance(body, dict) else 0,
                "path": body.get("path") if isinstance(body, dict) else None,
                "blocks": blocks if isinstance(blocks, list) else [],
                "url": body.get("url") if isinstance(body, dict) else None,
            },
        }
    if response.status_code == 400:
        return {
            "status": 400,
            "payload": {
                "error": "SIYUAN_TOKEN not set",
                "hint": "add it to ~/.omniroute/.env or export SIYUAN_TOKEN",
                "kind": "config_missing",
            },
        }
    if response.status_code == 401:
        return {
            "status": 401,
            "payload": {
                "error": "SiYuan rejected the token",
                "hint": "check SIYUAN_TOKEN",
                "kind": "auth",
            },
        }
    detail = _detail(response)
    return {
        "status": 502,
        "payload": {
            "error": f"SiYuan unreachable{f' ({detail})' if detail else ''}",
            "kind": "transport",
        },
    }


def describe(result: dict) -> Optional[str]:
    """One sentence for a log line; ``None`` when there is nothing to say."""
    if result.get("status") == 200:
        return None
    payload: Any = result.get("payload") or {}
    return str(payload.get("error") or "memory action failed")


__all__ = [
    "BLOCKS_TIMEOUT",
    "rag_status",
    "DEFAULT_BLOCKS",
    "MAX_BLOCKS",
    "RELOAD_TIMEOUT",
    "describe",
    "rag_reload",
    "set_client_factory",
    "siyuan_blocks",
]
