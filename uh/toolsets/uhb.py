"""UHB toolset (Niveau 2): the second brain's plan, its modes, and its state.

Three read-only questions, each answered without spending a token:

* ``uhb_status()`` -> ``GET {UH_UHB_URL}/status``, summarised as
  ``{connected, version, mode, harness, providers: [names], modes: [names]}``.
  Names only: the model gets the shape of the control plane, not its config.
* ``uhb_modes()`` -> ``GET /modes``: every mode with the globs it may read and
  write, the tools it may call, its model tier and whether it may pay. Prose
  (``system_prompt``) is dropped - it is another harness's internal
  instruction, and it can be long.
* ``uhb_route(prompt, mode?, complexity?)`` -> ``POST /route``: the plan UHB
  would follow - complexity, score, signals, reason, and the ordered chain. The
  first entry is the model that *would* answer; nothing is called.

Fail-closed by construction:

* the URL must be loopback (``UH_UHB_URL``, default ``http://127.0.0.1:8101``);
  an empty value disables the toolset, and a remote host is refused rather than
  contacted - UH never asks a remote "UHB" anything;
* the toolset is only built when a 1 s probe of ``GET /modes`` answers, so a UHB
  that is down - or older than this contract - contributes no tool at all and
  ``/tools`` shows nothing. Probing ``/modes`` rather than ``/status`` is the
  point: an old UHB would otherwise advertise tools that answer 404;
* a UHB that dies mid-session yields ``{"error": "uhb unreachable", "kind":
  "transport"}``: a tool result the model can read, never an exception inside
  ``run_turn``.

Errors carry a ``kind`` so the model can act differently: ``invalid_request``
(400 - fix the call), ``unroutable`` (409 - no model can serve this mode and
complexity; report it, do not retry), ``configuration`` (500 - UHB's setup is
broken), ``http`` (another status), ``invalid_response`` (not JSON).

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
TIMEOUT = 1.0  # the probe: a control plane answers fast or does not exist
CALL_TIMEOUT = 5.0  # a route decision reads the catalog, but stays local
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
    {
        "type": "function",
        "function": {
            "name": "uhb_modes",
            "description": (
                "List UHB's execution modes with their permissions: the path "
                "globs each may read and write, the paths it denies, the tools "
                "it may call, its model tier and whether it may use a paid "
                "model. Read-only, costs nothing."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "uhb_route",
            "description": (
                "Ask UHB which model it would use for a prompt, in a given mode, "
                "WITHOUT running anything. Returns the complexity, the reason "
                "and the ordered failover chain; the first entry is the model "
                "that would answer. No token is spent, no prompt is sent to a "
                "provider."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "The request to plan for."},
                    "mode": {
                        "type": "string",
                        "description": "UHB mode (chat, dev, review, observe). Defaults to UHB's own mode.",
                    },
                    "complexity": {
                        "type": "string",
                        "enum": ["trivial", "standard", "complex"],
                        "description": "Force a complexity instead of letting UHB classify the prompt.",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
]

#: The fields the model needs from a mode. ``system_prompt`` is dropped on
#: purpose: it is UHB's instruction, not UH's, and it is the longest field.
MODE_FIELDS = (
    "name",
    "description",
    "kind",
    "read",
    "write",
    "deny",
    "tools",
    "allow_paid",
    "max_turns",
)

#: HTTP status -> the action the model should take.
KINDS = {400: "invalid_request", 409: "unroutable", 500: "configuration"}


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
    # hostname, not netloc: "http://***@evil.example" has hostname
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


def compact_mode(entry: Dict[str, Any]) -> dict:
    """One mode: permissions and envelope, without the prose."""
    mode: Dict[str, Any] = {field: entry[field] for field in MODE_FIELDS if field in entry}
    model = entry.get("model")
    if isinstance(model, dict):
        mode["model"] = {key: model[key] for key in ("tier", "max_tokens") if key in model}
    return mode


def compact_decision(payload: Dict[str, Any]) -> dict:
    """One plan: what would answer, why, and the failover order.

    ``base_url`` and ``api_key_env`` are left out: they describe UHB's
    installation, not the decision, and the model cannot act on them.
    """
    chain: List[dict] = []
    raw_chain = payload.get("chain")
    if isinstance(raw_chain, list):
        for candidate in raw_chain:
            if isinstance(candidate, dict):
                chain.append(
                    {
                        "ref": _scalar(candidate.get("ref")),
                        "tier": _scalar(candidate.get("tier")),
                        "model_id": _scalar(candidate.get("model_id")),
                    }
                )
    signals = payload.get("signals")
    return {
        "mode": _scalar(payload.get("mode")),
        "complexity": _scalar(payload.get("complexity")),
        "score": payload.get("score"),
        "signals": signals if isinstance(signals, dict) else {},
        "allow_paid": payload.get("allow_paid"),
        "reason": _scalar(payload.get("reason")),
        "chosen": chain[0]["ref"] if chain else None,
        "chain": chain,
    }


def _json(response: httpx.Response) -> Optional[Dict[str, Any]]:
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _message(payload: Optional[Dict[str, Any]]) -> str:
    """UHB's own message, which names the accepted modes or complexities."""
    error = (payload or {}).get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return str(error or "")


def _unreachable() -> dict:
    return {"error": "uhb unreachable", "kind": "transport"}


def _invalid() -> dict:
    return {"error": "uhb returned invalid JSON", "kind": "invalid_response"}


def _failure(response: httpx.Response) -> dict:
    """Map a status code to the action the model should take."""
    detail = _message(_json(response))
    return {
        "error": detail or f"uhb HTTP {response.status_code}",
        "kind": KINDS.get(response.status_code, "http"),
        "status": response.status_code,
    }


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
    """True when a UHB exposing the ``/modes`` + ``/route`` contract answers.

    ``GET /modes`` is the probe on purpose: it exists only once that contract is
    deployed. An older UHB is then invisible rather than half-usable.
    """
    target = url or resolve_url()
    if not target:
        return False
    try:
        with _client(timeout, client) as http:
            response = http.get(f"{target}/modes")
    except Exception:  # noqa: BLE001 - a probe reports a state, never raises
        return False
    payload = _json(response)
    return response.status_code == 200 and isinstance((payload or {}).get("modes"), list)


class Uhbtoolset:
    """Three read-only tools: UHB's state, its modes, its plan."""

    name = "uhb"
    tools = TOOLS

    def __init__(
        self,
        url: str,
        timeout: float = CALL_TIMEOUT,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client = client
        #: The last plan UHB returned in this session. The Decision view reads it
        #: to report what the model actually asked, instead of guessing.
        self.last_route: Optional[dict] = None

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
        arguments = dict(args or {})
        if name == "uhb_status":
            return self.status()
        if name == "uhb_modes":
            return self.modes()
        if name == "uhb_route":
            return self.route(
                str(arguments.get("prompt") or ""),
                str(arguments.get("mode") or ""),
                str(arguments.get("complexity") or ""),
            )
        return {"error": f"unknown tool: {name}"}

    def _get(self, path: str) -> Dict[str, Any]:
        """GET ``path``: UHB's payload, or a tool-error dict carrying ``error``."""
        try:
            with _client(self.timeout, self.client) as http:
                response = http.get(f"{self.url}{path}")
        except Exception:  # noqa: BLE001 - a dead UHB is a tool error, not a crash
            return _unreachable()
        if response.status_code != 200:
            return _failure(response)
        payload = _json(response)
        if payload is None:
            return _invalid()
        return payload

    def status(self) -> dict:
        payload = self._get("/status")
        if "error" in payload:
            return payload
        return summarize(payload)

    def modes(self) -> dict:
        payload = self._get("/modes")
        if "error" in payload:
            return payload
        entries = payload.get("modes")
        if not isinstance(entries, list):
            return _invalid()
        return {"modes": [compact_mode(entry) for entry in entries if isinstance(entry, dict)]}

    def route(self, prompt: str, mode: str = "", complexity: str = "") -> dict:
        """The plan UHB would follow. Nothing is executed, nothing is spent."""
        text = (prompt or "").strip()
        if not text:
            return {"error": "prompt is required", "kind": "invalid_request"}
        body: Dict[str, Any] = {"prompt": text}
        if mode.strip():
            body["mode"] = mode.strip()
        if complexity.strip():
            body["complexity"] = complexity.strip()
        try:
            with _client(self.timeout, self.client) as http:
                response = http.post(f"{self.url}/route", json=body)
        except Exception:  # noqa: BLE001 - transport failure is a tool result
            return _unreachable()
        if response.status_code != 200:
            return _failure(response)
        payload = _json(response)
        if payload is None:
            return _invalid()
        plan = compact_decision(payload)
        self.last_route = plan
        return plan


__all__ = [
    "Uhbtoolset",
    "resolve_url",
    "probe",
    "summarize",
    "compact_mode",
    "compact_decision",
    "TOOLS",
    "DEFAULT_URL",
    "ENV_URL",
    "TIMEOUT",
    "CALL_TIMEOUT",
]
