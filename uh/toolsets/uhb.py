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
  ``run_turn``;
* every ``execute_code`` and every ``desktop_*`` call asks the operator first,
  under its own ``uhb:<tool>`` scope; with no stream wired the answer is a
  refusal, never a run. The read-only questions ask nothing - they spend nothing.

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
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

import httpx

ENV_URL = "UH_UHB_URL"
DEFAULT_URL = "http://127.0.0.1:8101"
TIMEOUT = 1.0  # the probe: a control plane answers fast or does not exist
#: The switch that exposes `execute_code` to the chat, and the tool it names.
EXECUTE_ENV = "UH_UHB_EXECUTE"
EXECUTE_TOOL = "execute_code"
#: Every tool UHB publishes under this prefix acts on the operator's machine -
#: clicks, keystrokes, screenshots, URLs. Visible in the catalogue, so UHB can
#: add a member without an edit here: the gate is the prefix, not a list.
DESKTOP_PREFIX = "desktop_"


def approval_scope(name: str) -> str:
    """The gate scope of one tool: ``uhb:<tool>``.

    One scope per tool, never the bare ``uhb``: the UI remembers "always allow"
    per scope, and a shared scope would make one answer authorise every tool.
    """
    return f"uhb:{name}"


def is_gated(name: str) -> bool:
    """True when a call must carry the operator's approval before it is sent.

    Two families ask first: ``execute_code`` (it runs a snippet as the operator's
    user) and every ``desktop_*`` tool (it acts through the OS: a click, a URL, a
    keystroke). Screenshots and window titles are gated too - they leak e-mail
    subjects and file names. The read-only questions (``uhb_status``,
    ``uhb_modes``, ``uhb_route``, ``describe_tools``) spend nothing and ask
    nothing.

    A prefix rather than an enumeration, and that is the security property: a
    tool UHB catalogues tomorrow as ``desktop_<something>`` is gated by this
    function without anyone remembering to add it here.
    """
    return name == EXECUTE_TOOL or name.startswith(DESKTOP_PREFIX)


#: The scope an approval is remembered under by the UI gate. Kept as a module
#: constant for the callers that name it; it is `approval_scope(EXECUTE_TOOL)`.
APPROVAL_SCOPE = approval_scope(EXECUTE_TOOL)

CALL_TIMEOUT = 5.0  # a route decision reads the catalog, but stays local
#: How long the client waits *past* the time a snippet was allowed to run. UHB
#: starts a child, waits for it, kills the tree on timeout, scans the run
#: directory for artifacts and only then answers: the answer cannot arrive
#: before the snippet ends. A client budget equal to the requested timeout cuts
#: a run that is working exactly as asked.
EXECUTE_MARGIN = 15.0
#: Bounds used only when a catalogue does not publish them. UHB publishes
#: ``timeout.default`` and ``timeout.maximum`` in the tool schema, and those are
#: preferred: a copy here would drift from the clamp UHB actually applies.
EXECUTE_DEFAULT_TIMEOUT = 30.0
EXECUTE_MAX_TIMEOUT = 300.0
EXECUTE_MIN_TIMEOUT = 1.0
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
    {
        "type": "function",
        "function": {
            "name": "describe_tools",
            "description": (
                "Look up what UHB can actually do: the tools it knows, with their "
                "description and JSON Schema of parameters, the modes that declare "
                "them, and whether the current mode allows each one (with the reason "
                "when it does not). Consultation only: this does not run anything, "
                "and an `available: true` here is information, not a permission - "
                "the call itself is checked again by UHB."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "One tool to detail. Omit to list every tool.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Mode to answer for. Defaults to the mode UHB serves.",
                    },
                },
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
    """Map a status code to the action the model should take.

    UHB is the authority: a 403 means the mode does not allow this, and the payload
    says ``type: forbidden`` in the same shape ``uh/agent.py`` uses when it refuses
    a tool it was never offered. The model reads one vocabulary, not two.
    """
    detail = _message(_json(response))
    forbidden = response.status_code == 403
    payload: dict = {
        "error": detail or f"uhb HTTP {response.status_code}",
        "kind": KINDS.get(response.status_code, "http"),
        "status": response.status_code,
    }
    payload["type"] = "forbidden" if forbidden else "tool_error"
    return payload


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


def approver_for(
    notify: Callable[[Dict[str, Any]], None]
) -> Callable[[str, Dict[str, Any]], bool]:
    """An approver bound to the UI gate: the operator answers, the answer decides.

    Imported lazily because `uh/mcp_http` imports this package: a module-level
    import here would close the cycle.
    """

    def approve(tool: str, arguments: Dict[str, Any]) -> bool:
        from ..mcp_http import GATE

        return GATE.ask(approval_scope(tool), tool, arguments, notify=notify)

    return approve


class Uhbtoolset:
    """Three read-only tools: UHB's state, its modes, its plan."""

    name = "uhb"
    tools = TOOLS

    def __init__(
        self,
        url: str,
        timeout: float = CALL_TIMEOUT,
        client: Optional[httpx.Client] = None,
        discover: bool = False,
        approver: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client = client
        #: Asks the operator before a snippet runs. `None` means the interface wired
        #: nothing, and then `execute_code` is refused rather than run: the design is
        #: fail-closed, so a missing approval control cannot become "it runs anyway".
        self.approver = approver
        #: Tools UHB catalogues that this file does not hardcode. Their names and
        #: schemas come from `GET /tools` at construction: a list here would be a
        #: second registry, and the two would drift the first time one changed.
        self.tools = list(TOOLS)
        self.remote: set[str] = set()
        #: Bounds published by UHB for `execute_code.timeout` (``default`` and
        #: ``maximum``), captured from the catalogue so this file never carries a
        #: second copy of the clamp. Empty until a catalogue is read.
        self.execute_limits: Dict[str, float] = {}
        if discover:
            self._discover()
        #: The last plan UHB returned in this session. The Decision view reads it
        #: to report what the model actually asked, instead of guessing.
        self.last_route: Optional[dict] = None

    @classmethod
    def from_environment(cls, probe_fn=None, notify=None) -> Optional["Uhbtoolset"]:
        """Build the toolset only when UHB answers. ``None`` means "no tools".

        ``notify`` is the turn's stream: with it, `execute_code` asks the operator
        before running and UHB checks the approval it is given. Without it, the tool
        stays advertised but refuses every call — a toolset built for a panel is not
        a permission to execute.
        """
        url = resolve_url()
        if url is None:
            return None
        check = probe_fn or probe
        if not check(url):
            return None
        # Production builds the catalogue into the toolset; tests construct directly,
        # so they are not forced into an extra request they did not script.
        return cls(url, discover=True, approver=approver_for(notify) if notify else None)

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
        if name == "describe_tools":
            return self.describe_tools(
                str(arguments.get("name") or ""), str(arguments.get("mode") or "")
            )
        if name in self.remote:
            if is_gated(name):
                refusal = self._approval_refusal(name, arguments)
                if refusal is not None:
                    return refusal
            # UHB decides: this only transports the call and its answer.
            return self._call_remote(name, arguments)
        return {"error": f"unknown tool: {name}", "type": "forbidden", "tool": name}

    def _approval_refusal(self, name: str, arguments: dict) -> Optional[dict]:
        """Refuse a call the operator did not authorise, in the shared vocabulary.

        The question is asked here, in the turn's thread, so the answer arrives while
        the tool call is waiting — and UHB still demands its own nonce afterwards for
        `execute_code`. Two checks, two authorities: the interface carries the
        decision, the backend enforces it.

        ``name`` is the tool actually asked for, and it is what the refusal reports:
        a refusal that names `execute_code` while the call was `desktop_click`
        would misdirect the operator's next answer.
        """
        if self.approver is None:
            return {
                "error": (f"{name} needs an approval and this interface wired "
                          "none: nothing was run"),
                "type": "forbidden",
                "kind": "forbidden",
                "status": 403,
                "tool": name,
                "reason": "approval_required",
            }
        try:
            allowed = bool(self.approver(name, arguments))
        except Exception:  # noqa: BLE001 - a broken notifier is a refusal, not a run
            allowed = False
        if allowed:
            return None
        return {
            "error": f"{name} was refused: nothing was run",
            "type": "forbidden",
            "kind": "forbidden",
            "status": 403,
            "tool": name,
            "reason": "approval_refused",
        }

    def _mint_approval(self) -> Optional[str]:
        """Ask UHB for the nonce one run is allowed to carry; `None` on any doubt.

        UHB is the authority here, so a refusal, a timeout or a payload without an id
        all mean the same thing: nothing runs. Returning an empty string instead
        would push the decision to the next layer, which is exactly what the nonce
        exists to prevent.
        """
        try:
            with _client(self.timeout, self.client) as http:
                response = http.post(
                    f"{self.url}/tools/approvals", json={"tool": EXECUTE_TOOL}
                )
        except Exception:  # noqa: BLE001 - no nonce means no run
            return None
        if response.status_code != 200:
            return None
        payload = _json(response)
        nonce = (payload or {}).get("approval_id") if isinstance(payload, dict) else None
        return str(nonce) if nonce else None

    # -- catalogue-driven tools --------------------------------------------- #
    def _discover(self) -> None:
        """Add the tools UHB catalogs that this file does not name.

        A tool already declared here (the three read-only ones, `describe_tools`)
        keeps its hand-written spec: its description mentions behaviour this client
        knows better than a generic string. Everything else is taken verbatim.
        """
        payload = self._get("/tools")
        entries = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            return
        known = {spec["function"]["name"] for spec in self.tools}
        #: `execute_code` runs a subprocess, so it is not offered on the generic
        #: route — and it is only offered at all when the operator said so. Two
        #: conditions, both required: the switch, and a catalogue that publishes it.
        execute_allowed = os.environ.get(EXECUTE_ENV, "").strip() == "1"
        for entry in entries:
            name = str((entry or {}).get("name") or "")
            parameters = (entry or {}).get("parameters")
            if not name or name in known or not isinstance(parameters, dict):
                continue
            in_process = (entry or {}).get("execution_type") == "in_process"
            if not in_process and not (name == EXECUTE_TOOL and execute_allowed):
                continue
            self.tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": str(entry.get("description") or "")[:1000],
                        "parameters": parameters,
                    },
                }
            )
            self.remote.add(name)
            if name == EXECUTE_TOOL:
                self._read_execute_limits(parameters)

    def _read_execute_limits(self, parameters: dict) -> None:
        """Keep the `timeout` bounds UHB publishes, so the wait matches its clamp."""
        spec = (parameters.get("properties") or {}).get("timeout")
        if not isinstance(spec, dict):
            return
        limits: Dict[str, float] = {}
        for key in ("default", "maximum"):
            try:
                limits[key] = float(spec[key])
            except (KeyError, TypeError, ValueError):
                continue
        self.execute_limits = limits

    def execute_timeout(self, arguments: dict) -> float:
        """Client budget for one `execute_code` call: the snippet's time plus margin.

        UHB answers only after the child is over (killed on timeout, artifacts
        scanned), so the transport must outwait the snippet. The requested value is
        bounded by what the catalogue published - not by a constant copied here.
        """
        default = self.execute_limits.get("default", EXECUTE_DEFAULT_TIMEOUT)
        ceiling = self.execute_limits.get("maximum", EXECUTE_MAX_TIMEOUT)
        raw = arguments.get("timeout")
        try:
            requested = float(raw) if raw is not None else default
        except (TypeError, ValueError):
            requested = default
        if requested <= 0:
            requested = default
        return min(max(requested, EXECUTE_MIN_TIMEOUT), ceiling) + EXECUTE_MARGIN

    def _call_timeout(self, name: str, arguments: dict) -> float:
        """Per-call transport timeout: only `execute_code` gets the longer budget."""
        if name == EXECUTE_TOOL:
            return self.execute_timeout(arguments)
        return self.timeout

    def _call_remote(self, name: str, arguments: dict) -> dict:
        """POST an argument object to UHB.

        `execute_code` has its own route and a body shape that is not a parameter
        object; everything else goes to the generic one. The transport differs,
        the authority does not: UHB checks the mode in both cases.
        """
        path = "/tools/execute" if name == EXECUTE_TOOL else f"/tools/{name}"
        body: Dict[str, Any] = dict(arguments or {})
        if name == EXECUTE_TOOL:
            # The transcript draws a figure from a data URL, never from a path, and
            # UH refuses to read a file the tool merely named (tests/test_tool_images:
            # "a path without a data url is not read"). UHB inlines only on request,
            # so the request is made here: this is a transport detail of the chat,
            # not a choice left to the model. Above UHB's 1.5 MB inline limit the
            # image stays a path — UHB says so in its notes.
            body["include_base64"] = True
            # The operator's answer was carried by `call()`. UHB wants its own proof:
            # a nonce it mints, single use, and checks itself. Without one, nothing
            # runs — including here, rather than sending a body that would be refused.
            nonce = self._mint_approval()
            if nonce is None:
                return {
                    "error": ("execute_code needs an approval nonce and UHB did not "
                              "issue one: nothing was executed"),
                    "type": "forbidden",
                    "kind": "forbidden",
                    "status": 403,
                    "tool": EXECUTE_TOOL,
                    "reason": "approval_required",
                }
            body["approval_id"] = nonce
        try:
            with _client(self._call_timeout(name, arguments), self.client) as http:
                response = http.post(f"{self.url}{path}", json=body)
        except Exception:  # noqa: BLE001 - transport failure is a tool result
            return _unreachable()
        if response.status_code >= 400:
            return _failure(response)
        payload = _json(response)
        return payload if isinstance(payload, dict) else _invalid()

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

    def describe_tools(self, name: str = "", mode: str = "") -> dict:
        """What UHB can do, and whether this mode allows it. Never executes anything.

        The catalogue is UHB's: this method asks for it and passes it through
        untouched. A copy in `uh/` would be a second source of truth, and the two
        would drift the first time a schema changed.
        """
        path = f"/tools/{name.strip()}" if name.strip() else "/tools"
        wanted = mode.strip()
        if wanted:
            path = f"{path}?mode={wanted}"
        return self._get(path)


__all__ = [
    "Uhbtoolset",
    "approver_for",
    "approval_scope",
    "is_gated",
    "APPROVAL_SCOPE",
    "DESKTOP_PREFIX",
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
    "EXECUTE_MARGIN",
    "EXECUTE_DEFAULT_TIMEOUT",
    "EXECUTE_MAX_TIMEOUT",
    "EXECUTE_MIN_TIMEOUT",
]
