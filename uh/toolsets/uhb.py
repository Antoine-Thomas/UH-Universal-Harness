"""UHB toolset (Niveau 1): one read-only look at the second brain's state.

The tool answers one question - "is UHB up, and what is it running?" - without
pouring UHB's whole payload into the model's context:

* ``uhb_status()`` -> ``GET {UH_UHB_URL}/status``, summarised as
  ``{connected, version, mode, harness, providers: [names], modes: [names]}``.
  Names only: the model gets the shape of the control plane, not its config.

Fail-closed by construction:

* the URL must be loopback (``UH_UHB_URL``, default ``http://127.0.0.1:8101``);
  an empty value disables the toolset, and a remote host is refused rather than
  contacted - UH never asks a remote "UHB" anything;
* the toolset is only built when a 1 s probe of ``GET /status`` answers, so a
  UHB that is down contributes no tool at all and ``/tools`` shows nothing;
* a UHB that dies mid-session yields ``{"error": "uhb unreachable"}``: a tool
  result the model can read, never an exception inside ``run_turn``.

No dependency beyond ``httpx``, which UH already requires.
"""

from __future__ import annotations

import ipaddress
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

import httpx

ENV_URL = "UH_UHB_URL"
DEFAULT_URL = "http://127.0.0.1:8101"
TIMEOUT = 1.0
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "uhb_status",
            "description": (
                "Check whether UHB (the second brain / control plane) is up, and "
                "return its version, active mode, harness, and the names of its "
                "providers and modes. Read-only: no model is called, nothing is "
                "spent, no prompt leaves the machine."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# --------------------------------------------------------------------------- #
# URL policy
# --------------------------------------------------------------------------- #
def _is_loopback(host: str) -> bool:
    raw = (host or "").strip().lower()
    if raw in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(raw.strip("[]")).is_loopback
    except ValueError:
        return False


def resolve_url() -> Optional[str]:
    """``UH_UHB_URL`` when it is a loopback http(s) URL, ``None`` otherwise.

    ``None`` means "no toolset": an empty variable disables UHB on purpose, and
    a non-loopback host is refused instead of being contacted.
    """
    raw = os.environ.get(ENV_URL, DEFAULT_URL)
    value = (raw or "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"}:
        return None
    # hostname, not netloc: "http://127.0.0.1@evil.example" has hostname
    # evil.example, and netloc would have let it through.
    if not _is_loopback(parts.hostname or ""):
        return None
    return value.rstrip("/")


# --------------------------------------------------------------------------- #
# payload compaction
# --------------------------------------------------------------------------- #
def _scalar(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


def _names(value: Any) -> List[str]:
    """Names only, from a list of entries or a name -> entry mapping."""
    names: List[str] = []
    if isinstance(value, dict):
        for key, entry in value.items():
            if isinstance(entry, dict):
                names.append(str(entry.get("name") or entry.get("id") or key))
            else:
                names.append(str(key))
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("id") or entry.get("ref")
                if name:
                    names.append(str(name))
            elif isinstance(entry, str):
                names.append(entry)
    return list(dict.fromkeys(names))


def _harness(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        name = value.get("name") or value.get("id")
        version = value.get("version")
        if name and version:
            return f"{name} {version}"
        return str(name) if name else None
    return _scalar(value)


def summarize(payload: Dict[str, Any]) -> dict:
    """The compact status: what is up, what it runs, nothing else."""
    return {
        "connected": True,
        "version": _scalar(payload.get("version")),
        "mode": _scalar(payload.get("mode")),
        "harness": _harness(payload.get("harness")),
        "providers": _names(payload.get("providers")),
        "modes": _names(payload.get("modes")),
    }


def _json(response: httpx.Response) -> Optional[Dict[str, Any]]:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
@contextmanager
def _client(timeout: float, client: Optional[httpx.Client]) -> Iterator[httpx.Client]:
    """Reuse an injected client (tests), or own a short-lived one per call."""
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=timeout) as made:
        yield made


def probe(
    url: Optional[str] = None,
    timeout: float = TIMEOUT,
    client: Optional[httpx.Client] = None,
) -> bool:
    """True when a UHB answers ``GET /status`` with a JSON object."""
    target = url or resolve_url()
    if not target:
        return False
    try:
        with _client(timeout, client) as http:
            response = http.get(f"{target}/status")
    except Exception:  # noqa: BLE001 - a probe reports a state, never raises
        return False
    return response.status_code == 200 and _json(response) is not None


class Uhbtoolset:
    """One read-only tool: UHB's state, compacted for the model's context."""

    name = "uhb"
    tools = TOOLS

    def __init__(
        self,
        url: str,
        timeout: float = TIMEOUT,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client = client

    @classmethod
    def from_environment(cls, probe_fn=None) -> Optional["Uhbtoolset"]:
        """Build the toolset only when UHB answers. ``None`` means "no tools"."""
        url = resolve_url()
        if url is None:
            return None
        check = probe_fn or probe
        if not check(url):
            return None
        return cls(url)

    # -- tools -------------------------------------------------------------- #
    def call(self, name: str, args: Optional[dict] = None) -> dict:
        if name != "uhb_status":
            return {"error": f"unknown tool: {name}"}
        return self.status()

    def status(self) -> dict:
        try:
            with _client(self.timeout, self.client) as http:
                response = http.get(f"{self.url}/status")
        except Exception:  # noqa: BLE001 - a dead UHB is a tool error, not a crash
            return {"error": "uhb unreachable"}
        if response.status_code != 200:
            return {"error": f"uhb HTTP {response.status_code}"}
        payload = _json(response)
        if payload is None:
            return {"error": "uhb returned invalid JSON"}
        return summarize(payload)


__all__ = [
    "Uhbtoolset",
    "resolve_url",
    "probe",
    "summarize",
    "TOOLS",
    "DEFAULT_URL",
    "ENV_URL",
    "TIMEOUT",
]
