"""The UHB run lifecycle, seen from UH - a proxy, never a decision.

UH shows the runs and passes on the operator's gesture; UHB decides. That is why this
module contains no ``rmtree`` and no retention rule: it speaks HTTP to
``GET /runs`` and ``POST /runs``, and reports ``ok: False`` with a readable reason when
UHB is not there. A cockpit that hides the body because a service is down lies twice.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_URL = "http://127.0.0.1:8101"
ENV_URL = "UH_UHB_URL"
TIMEOUT = 1.5


def base_url() -> str:
    return (os.environ.get(ENV_URL) or DEFAULT_URL).rstrip("/")


def _call(method: str, path: str, payload: Optional[dict] = None) -> Dict[str, Any]:
    url = f"{base_url()}{path}"
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            if method == "GET":
                response = client.get(url)
            else:
                response = client.post(url, json=payload or {})
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"UHB injoignable ({type(exc).__name__})", "runs": []}
    if response.status_code >= 400:
        detail = ""
        try:
            detail = str((response.json() or {}).get("error", {}).get("message") or "")
        except ValueError:
            detail = response.text[:200]
        return {"ok": False, "reason": detail or f"HTTP {response.status_code}", "runs": []}
    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "reason": "réponse illisible", "runs": []}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "réponse inattendue", "runs": []}
    data.setdefault("ok", True)
    return data


def status() -> Dict[str, Any]:
    """What ``GET /runs`` says: count, weight, policy, recent runs."""
    data = _call("GET", "/runs")
    if not data.get("ok"):
        return data
    data["runs"] = list(data.get("recent") or [])
    return data


def close(run: str, keep: bool, summary: str = "") -> Dict[str, Any]:
    return _call(
        "POST", "/runs", {"action": "close", "run": run, "keep": bool(keep), "summary": summary}
    )


def prune(apply: bool = False) -> Dict[str, Any]:
    """A dry run unless ``apply`` - and the interface only sends ``apply`` after a click."""
    return _call("POST", "/runs", {"action": "prune", "apply": bool(apply)})


def restore(revision: str) -> Dict[str, Any]:
    return _call("POST", "/runs", {"action": "restore", "revision": revision})


def close_many(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Close the runs of one finished turn: ``[{run, keep}, …]``.

    Never raises: a closed run is bookkeeping, and bookkeeping must not cost a turn.
    """
    results: List[Dict[str, Any]] = []
    for entry in entries:
        run_id = str(entry.get("run") or "").strip()
        if not run_id:
            continue
        try:
            results.append(close(run_id, bool(entry.get("keep"))))
        except Exception as exc:  # noqa: BLE001 - the turn is already answered
            results.append({"ok": False, "reason": f"{type(exc).__name__}: {exc}", "run": run_id})
    return results


__all__ = [
    "DEFAULT_URL",
    "ENV_URL",
    "TIMEOUT",
    "base_url",
    "close",
    "close_many",
    "prune",
    "restore",
    "status",
]
