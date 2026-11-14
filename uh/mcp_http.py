"""MCP over HTTP: the transport the stdio client could not speak.

Positron, an IDE, a hosted tool server: those are addresses, not commands. This
module adds the wire, and nothing else - same JSON-RPC surface (``initialize``,
``notifications/initialized``, ``tools/list``, ``tools/call``), same failure
vocabulary, same ``mcp__<server>__<tool>`` names.

Two shapes are supported, in the order the specification introduced them:

* **Streamable HTTP** (2025-03-26): one endpoint. A JSON-RPC message is POSTed
  with ``Accept: application/json, text/event-stream``; the answer is either a
  JSON body or an SSE stream that carries it. The session id comes back in
  ``Mcp-Session-Id`` and is echoed on every later request.
* **HTTP+SSE** (2024-11-05, what Positron ships): a GET opens one long-lived
  event stream whose first ``endpoint`` event names the URL that accepts POSTs.
  Responses arrive on that stream; the POST answers 202.

Rules that come from the security review, not from convenience:

* redirects are never followed (``follow_redirects=False``): an approved address
  must not become another one after a 302;
* the token travels in ``Authorization: Bearer`` and is never logged, never
  returned, and never written to ``config.toml``;
* **a remote tool is never called on its own.** :func:`build_toolsets` returns
  nothing without an approver, and every ``tools/call`` asks it first. The web
  cockpit answers that question with a button; a caller without one (the console)
  simply does not get remote tools, which is the honest state.
* a client that was connected and stops answering is *disconnected*, not
  "idle": the watchdog probes, reports, and reconnects with a backoff.

Nothing here starts a process and nothing installs anything.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from . import urlguard
from .config import Config
from .config import load as load_config
from .mcp import (
    DEFAULT_TIMEOUT,
    START_TIMEOUT,
    MCPError,
    _render,
    _truncate,
    exposed_name,
    redact,
)

#: What the handshake advertises. A server that answers another revision is
#: accepted: the three methods this client uses are identical across revisions.
PROTOCOL_VERSION = "2025-03-26"

#: How long a failed server stays failed. Same idea as the stdio registry: the
#: cockpit polls, and a dead address must not mean a timeout on every poll.
RETRY_AFTER = 30.0

#: The watchdog's period and the backoff it applies when reconnecting.
WATCH_INTERVAL = 20.0
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0

#: A person pressing a button must answer in this window; past it, the call is
#: refused (fail-closed), not left hanging.
APPROVAL_TIMEOUT = 120.0

LABELS = {
    "configured": "configuré",
    "connecting": "connexion en cours",
    "connected": "connecté",
    "auth": "jeton refusé",
    "unreachable": "injoignable",
    "incompatible": "protocole incompatible",
    "timeout": "délai dépassé",
    "disconnected": "coupure détectée",
    "reconnecting": "reconnexion en cours",
    "invalid": "adresse refusée",
}

ADVICE = {
    "auth": "Le serveur a répondu qu'il refuse le jeton. Ouvre son réglage d'accès, "
    "copie le jeton qu'il affiche, puis enregistre-le dans « Connexions ».",
    "unreachable": "Rien n'écoute à cette adresse. Vérifie que le serveur est démarré "
    "et que le port est le bon.",
    "incompatible": "L'adresse répond, mais pas comme un serveur MCP. Vérifie qu'il "
    "s'agit bien de l'URL MCP (souvent /mcp ou /sse) et non de l'interface web de l'outil.",
    "invalid": "L'adresse enregistrée n'est plus acceptée : corrige-la dans "
    "« Connexions ».",
    "timeout": "Le serveur n'a pas répondu dans le délai. Il est peut-être occupé : "
    "réessaie, ou augmente le délai dans config.toml.",
    "disconnected": "La connexion s'est coupée. UH réessaie tout seul, puis te le dira.",
}


class ApprovalRequired(MCPError):
    """A remote tool was asked for, and no one is there to approve it."""


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class HTTPServer:
    """One ``[[mcp.servers]]`` entry with ``transport = "http"`` (or ``sse``)."""

    name: str
    url: str
    transport: str = "http"  # "http" = streamable, "sse" = legacy event stream
    token: str = field(default="", repr=False)
    timeout: float = DEFAULT_TIMEOUT
    allow: Optional[Tuple[str, ...]] = None
    insecure: bool = False

    def key(self) -> Tuple[Any, ...]:
        return (self.name, self.url, self.transport, self.token, self.allow)


def _secrets_for(name: str) -> Dict[str, str]:
    try:
        from .mcp_cloud import read_secrets

        return dict(read_secrets().get(name, {}))
    except Exception:  # noqa: BLE001 - a broken secrets file must not hide the entry
        return {}


def servers_from_config(config: Optional[Config] = None) -> List[HTTPServer]:
    """Every HTTP/SSE entry, validated enough to be contacted."""
    cfg = config if config is not None else load_config()
    section = cfg.tables.get("mcp") or {}
    raw = section.get("servers") if isinstance(section, dict) else None
    if not isinstance(raw, list):
        return []
    out: List[HTTPServer] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        transport = str(entry.get("transport") or "").lower()
        if transport not in {"http", "sse"}:
            continue
        name = str(entry.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        url = str(entry.get("url") or "").strip()
        try:
            checked = urlguard.check(url, allow_insecure=bool(entry.get("insecure")))
        except urlguard.URLRejected:
            # A hand-edited entry with a scheme UH never fetches: it stays in the
            # config (the panel shows it as refused) and is never contacted.
            continue
        url = checked.url
        allow_raw = entry.get("allow")
        allow = tuple(str(item) for item in allow_raw) if isinstance(allow_raw, list) else None
        timeout_raw = entry.get("timeout", DEFAULT_TIMEOUT)
        timeout = float(timeout_raw) if isinstance(timeout_raw, (int, float)) else DEFAULT_TIMEOUT
        out.append(
            HTTPServer(
                name=name,
                url=url,
                transport=transport,
                token=str(_secrets_for(name).get("MCP_HTTP_TOKEN") or ""),
                timeout=timeout,
                allow=allow,
                insecure=bool(entry.get("insecure")),
            )
        )
    return out


def explain(kind: str, detail: str = "") -> str:
    """One sentence a person can act on, plus the technical detail behind it."""
    advice = ADVICE.get(kind) or ADVICE["unreachable"]
    said = (detail or "").strip()
    return f"{advice}" + (f" (détail : {said})" if said else "")


# --------------------------------------------------------------------------- #
# protocol
# --------------------------------------------------------------------------- #
def _parse_sse(text: str) -> List[Dict[str, Any]]:
    """``event:``/``data:`` blocks -> the JSON payloads they carry."""
    events: List[Dict[str, Any]] = []
    kind = ""
    data: List[str] = []
    for line in text.splitlines():
        if line.startswith(":"):
            continue
        if not line.strip():
            if data:
                blob = "\n".join(data)
                try:
                    payload = json.loads(blob)
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    events.append({"event": kind or "message", "data": payload})
                elif kind == "endpoint":
                    events.append({"event": "endpoint", "data": {"endpoint": blob}})
            kind, data = "", []
            continue
        if line.startswith("event:"):
            kind = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        blob = "\n".join(data)
        try:
            payload = json.loads(blob)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            events.append({"event": kind or "message", "data": payload})
    return events


class HTTPClient:
    """One HTTP/SSE MCP server, and the request loop in front of it.

    One request in flight at a time, like the stdio client: MCP servers are
    usually a single script, and pipelining buys ordering bugs.
    """

    def __init__(self, server: HTTPServer) -> None:
        self.server = server
        self.endpoint = server.url
        # Positron and the other 2024-11-05 servers publish one stream at
        # ``/sse`` and take POSTs elsewhere. An entry recorded as plain "http"
        # with such an address is that shape, not a Streamable HTTP one.
        path = server.url.split("?", 1)[0].rstrip("/").lower()
        self.transport = "sse" if (server.transport == "sse" or path.endswith("/sse")) else "http"
        self.session_id = ""
        self.protocol_version = ""
        self.server_info: Dict[str, Any] = {}
        self.tools: List[Dict[str, Any]] = []
        self.last_seen = 0.0
        self.last_error = ""
        self.state = "configured"
        self._lock = threading.Lock()
        self._next_id = 0
        self._sse: Optional[httpx.Response] = None
        self._sse_ctx: Optional[Any] = None
        self._sse_client: Optional[httpx.Client] = None
        self._sse_thread: Optional[threading.Thread] = None
        self._events: Dict[int, Dict[str, Any]] = {}
        self._condition = threading.Condition()
        self._closed = False
        self._refused_redirects: List[str] = []

    # -- transport --------------------------------------------------------- #
    def _headers(self, *, accept: str) -> Dict[str, str]:
        headers = {"accept": accept, "content-type": "application/json"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.server.token:
            headers["authorization"] = f"Bearer {self.server.token}"
        return headers

    def _timeout(self, override: Optional[float] = None) -> httpx.Timeout:
        read = override if override is not None else self.server.timeout
        return httpx.Timeout(connect=min(10.0, read), read=read, write=10.0, pool=5.0)

    def _check(self, response: httpx.Response) -> None:
        if response.status_code in {301, 302, 303, 307, 308}:
            self._refused_redirects.append(response.headers.get("location", ""))
            raise MCPError(
                f"le serveur '{self.server.name}' redirige vers "
                f"{response.headers.get('location', '?')} : redirection refusée",
                kind="transport",
            )
        if response.status_code in {401, 403}:
            raise MCPError(
                f"le serveur '{self.server.name}' a refusé le jeton "
                f"(HTTP {response.status_code})",
                kind="auth",
            )
        if response.status_code == 404:
            raise MCPError(
                f"le serveur '{self.server.name}' ne connaît pas {self.endpoint} "
                "(HTTP 404)",
                kind="incompatible",
            )
        if response.status_code >= 500:
            raise MCPError(
                f"le serveur '{self.server.name}' a répondu HTTP {response.status_code}",
                kind="transport",
            )

    @staticmethod
    def _transport_error(server: str, exc: Exception) -> MCPError:
        if isinstance(exc, httpx.TimeoutException):
            return MCPError(f"'{server}' n'a pas répondu à temps", kind="timeout")
        if isinstance(exc, httpx.RemoteProtocolError):
            return MCPError(
                f"'{server}' a coupé la connexion au milieu d'une réponse", kind="transport"
            )
        return MCPError(f"'{server}' est injoignable ({type(exc).__name__})", kind="unreachable")

    def _post(self, message: Dict[str, Any], *, timeout: Optional[float] = None) -> httpx.Response:
        try:
            return httpx.post(
                self.endpoint,
                json=message,
                headers=self._headers(accept="application/json, text/event-stream"),
                timeout=self._timeout(timeout),
                follow_redirects=False,
                # A proxy in the environment must not see a loopback MCP token.
                trust_env=False,
            )
        except (httpx.HTTPError, OSError) as exc:
            raise self._transport_error(self.server.name, exc) from exc

    def _open_stream(self, timeout: float) -> None:
        """Legacy HTTP+SSE: one GET, then responses come down this pipe."""
        self._sse_client = httpx.Client(
            timeout=httpx.Timeout(connect=min(10.0, timeout), read=None, write=10.0, pool=5.0),
            follow_redirects=False,
            trust_env=False,
        )
        try:
            # The context manager owns the connection: dropping the object closes
            # the stream under the reader (a bare ``.__enter__()`` does not).
            self._sse_ctx = self._sse_client.stream(
                "GET",
                self.endpoint,
                headers={**self._headers(accept="text/event-stream"), "content-type": "text/plain"},
            )
            self._sse = self._sse_ctx.__enter__()
        except (httpx.HTTPError, OSError) as exc:
            self._close_stream()
            raise self._transport_error(self.server.name, exc) from exc
        code = self._sse.status_code
        if code in {401, 403}:
            self._close_stream()
            raise MCPError(
                f"le serveur '{self.server.name}' a refusé le jeton (HTTP {code})",
                kind="auth",
            )
        if code != 200:
            self._close_stream()
            raise MCPError(
                f"le serveur '{self.server.name}' a répondu HTTP {code} sur le flux SSE",
                kind="incompatible" if code < 500 else "transport",
            )
        self._sse_thread = threading.Thread(
            target=self._pump, name=f"mcp-http-{self.server.name}", daemon=True
        )
        self._sse_thread.start()

    def _pump(self) -> None:
        """Read the event stream, deliver responses, notice the pipe closing."""
        response = self._sse
        if response is None:  # pragma: no cover - _open_stream guarantees it
            return
        kind = ""
        data: List[str] = []
        try:
            for line in response.iter_lines():
                if self._closed:
                    break
                if not line.strip():
                    if data:
                        self._deliver(kind, data)
                    kind, data = "", []
                    continue
                if line.startswith("event:"):
                    kind = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
        except Exception as exc:  # noqa: BLE001 - StreamClosed is not an HTTPError
            self.last_error = f"flux interrompu ({type(exc).__name__})"
        finally:
            self._close_stream()
            if not self._closed:
                # A cut in the middle of a session, not a normal close.
                self.state = "disconnected"
                self.last_error = self.last_error or "le flux SSE s'est fermé"
                with self._condition:
                    self._condition.notify_all()

    def _deliver(self, kind: str, data: List[str]) -> None:
        blob = "\n".join(data)
        try:
            payload = json.loads(blob)
        except ValueError:
            if kind == "endpoint":
                self.endpoint = httpx.URL(self.endpoint).join(blob).__str__()
            return
        if kind == "endpoint" and isinstance(payload, str):
            self.endpoint = httpx.URL(self.endpoint).join(payload).__str__()
            return
        if not isinstance(payload, dict):
            return
        request_id = payload.get("id")
        if isinstance(request_id, int):
            with self._condition:
                self._events[request_id] = payload
                self._condition.notify_all()

    def _close_stream(self) -> None:
        context, response, client = self._sse_ctx, self._sse, self._sse_client
        self._sse_ctx = None
        self._sse = None
        self._sse_client = None
        for closer in (response, client):
            if closer is not None:
                try:
                    closer.close()
                except Exception:  # noqa: BLE001 - closing is not a failure path
                    pass
        if context is not None:
            try:
                context.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - closing is not a failure path
                pass

    # -- json-rpc ---------------------------------------------------------- #
    def _request(
        self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: Optional[float] = None
    ) -> Dict[str, Any]:
        limit = timeout if timeout is not None else self.server.timeout
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            message: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                message["params"] = params
            if self.transport == "sse":
                return self._request_over_stream(request_id, method, message, limit)
            response = self._post(message, timeout=limit)
            self._check(response)
            session = response.headers.get("mcp-session-id")
            if session:
                self.session_id = session
            content_type = (response.headers.get("content-type") or "").lower()
            if "text/event-stream" in content_type:
                for event in _parse_sse(response.text):
                    payload = event["data"]
                    if payload.get("id") == request_id:
                        return self._result(event["data"], method)
                raise MCPError(
                    f"le serveur '{self.server.name}' n'a pas renvoyé de réponse à {method}",
                    kind="incompatible",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise MCPError(
                    f"le serveur '{self.server.name}' a répondu autre chose que du JSON "
                    f"(HTTP {response.status_code})",
                    kind="incompatible",
                ) from exc
            if not isinstance(payload, dict):
                raise MCPError(
                    f"le serveur '{self.server.name}' a répondu un JSON qui n'est pas JSON-RPC",
                    kind="incompatible",
                )
            return self._result(payload, method)

    def _request_over_stream(
        self, request_id: int, method: str, message: Dict[str, Any], limit: float
    ) -> Dict[str, Any]:
        response = self._post(message, timeout=limit)
        self._check(response)
        deadline = time.monotonic() + limit
        with self._condition:
            while request_id not in self._events:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPError(
                        f"le serveur '{self.server.name}' n'a pas répondu à {method} "
                        f"en {limit:.0f}s",
                        kind="timeout",
                    )
                if self.state == "disconnected" and self._sse is None:
                    raise MCPError(
                        f"la connexion à '{self.server.name}' s'est coupée pendant {method}",
                        kind="transport",
                    )
                self._condition.wait(timeout=min(remaining, 1.0))
            payload = self._events.pop(request_id)
        return self._result(payload, method)

    def _result(self, payload: Dict[str, Any], method: str) -> Dict[str, Any]:
        if "error" in payload:
            error = payload.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else None
            raise MCPError(
                f"le serveur '{self.server.name}' a refusé {method}: {detail or error}",
                kind="server",
                code=code,
            )
        result = payload.get("result")
        self.last_seen = time.monotonic()
        return result if isinstance(result, dict) else {}

    def _notify(self, method: str) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if self.transport == "sse":
            if self._sse is None:
                return
            self._post(message)
            return
        self._post(message)

    # -- lifecycle --------------------------------------------------------- #
    def start(self, *, limit: float | None = None) -> "HTTPClient":
        """Handshake, then the tool list. Raises :class:`MCPError`.

        ``limit`` caps the whole handshake (the popup promises five seconds);
        without it the configured timeout applies, floored by START_TIMEOUT.
        """
        budget = limit if limit is not None else max(self.server.timeout, START_TIMEOUT)
        self.state = "connecting"
        self._closed = False
        try:
            if self.transport == "sse":
                self._open_stream(timeout=budget)
                # Wait for the endpoint event: without it there is nowhere to POST.
                deadline = time.monotonic() + budget
                while self.endpoint == self.server.url and time.monotonic() < deadline:
                    time.sleep(0.05)
                    if self.state == "disconnected":
                        break
            result = self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "uh", "version": _version()},
                },
                timeout=budget,
            )
        except MCPError:
            self.state = "configured"
            self.close()
            raise
        self.protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        self._notify("notifications/initialized")
        self.tools = self._list_tools()
        self.state = "connected"
        self.last_seen = time.monotonic()
        return self

    @property
    def alive(self) -> bool:
        if self.state != "connected":
            return False
        if self.transport == "sse":
            return self._sse is not None and not self._closed
        return not self._closed

    def close(self) -> None:
        self._closed = True
        self._close_stream()
        with self._condition:
            self._condition.notify_all()

    def _list_tools(self) -> List[Dict[str, Any]]:
        result = self._request("tools/list", {})
        raw = result.get("tools")
        tools = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        if self.server.allow is not None:
            allowed = set(self.server.allow)
            tools = [item for item in tools if str(item.get("name") or "") in allowed]
        return [item for item in tools if item.get("name")]

    # -- calls ------------------------------------------------------------- #
    def call(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = self._request("tools/call", {"name": tool, "arguments": arguments or {}})
        text, structured = _render(result)
        text = _truncate(redact(text))
        payload: Dict[str, Any] = {"server": self.server.name, "tool": tool, "text": text}
        if structured is not None:
            payload["structured"] = structured
        if result.get("isError"):
            return {
                **payload,
                "error": text or "the MCP server reported an error",
                "kind": "tool",
            }
        return payload

    def probe(self) -> bool:
        """Cheap liveness check. Never raises: reports through ``state``."""
        try:
            self._request("ping", {})
        except MCPError as exc:
            if exc.kind == "server" and getattr(exc, "details", {}).get("code") == -32601:
                # ping is optional in the spec: a server that answers "not found"
                # is reachable, which is the only thing the probe asks.
                self.last_seen = time.monotonic()
                return True
            self.last_error = redact(str(exc))
            self.state = "disconnected" if self.state == "connected" else self.state
            return False
        self.last_seen = time.monotonic()
        self.state = "connected"
        return True

    def backoff_due(self, failed: Optional[Tuple[float, str, int]], now: float) -> bool:
        """Is it time to try this server again? 1, 2, 4 … 60 seconds."""
        if failed is None:
            return True
        return (now - failed[0]) >= compute_backoff(failed[2])

    def status(self) -> Dict[str, Any]:
        return {
            "name": self.server.name,
            "transport": self.transport,
            "url": self.server.url,
            "connected": self.state == "connected",
            "state": self.state,
            "label": LABELS.get(self.state, self.state),
            "protocol": self.protocol_version,
            "server": self.server_info.get("name") or "",
            "version": self.server_info.get("version") or "",
            "tools": [str(tool.get("name")) for tool in self.tools],
            "reason": self.last_error,
            "advice": explain("disconnected", self.last_error) if self.last_error else "",
            "insecure": self.server.insecure,
        }


def _version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # pragma: no cover
        return "0"


# --------------------------------------------------------------------------- #
# registry: one client per entry, a backoff after a failure
# --------------------------------------------------------------------------- #
_CLIENTS: Dict[Tuple[Any, ...], HTTPClient] = {}
_FAILED: Dict[Tuple[Any, ...], Tuple[float, str, int]] = {}  # (when, why, attempts)
_LOCK = threading.Lock()
_WATCH: Optional[threading.Thread] = None
_WATCH_STOP = threading.Event()


def client_for(server: HTTPServer, *, force: bool = False) -> HTTPClient:
    """The live client for this entry, starting it once. Raises :class:`MCPError`."""
    key = server.key()
    with _LOCK:
        existing = _CLIENTS.get(key)
        if existing is not None and existing.alive and not force:
            return existing
        if existing is not None:
            existing.close()
            _CLIENTS.pop(key, None)
        failed = _FAILED.get(key)
        if failed is not None and not force:
            age = time.monotonic() - failed[0]
            if age < RETRY_AFTER:
                raise MCPError(
                    f"'{server.name}' n'est pas disponible (nouvel essai dans "
                    f"{RETRY_AFTER - age:.0f}s) : {redact(failed[1])}",
                    kind="unavailable",
                )
    client = HTTPClient(server)
    try:
        client.start()
    except MCPError as exc:
        client.close()
        with _LOCK:
            attempts = (_FAILED.get(key) or (0.0, "", 0))[2] + 1
            _FAILED[key] = (time.monotonic(), str(exc), attempts)
        raise
    with _LOCK:
        _CLIENTS[key] = client
        _FAILED.pop(key, None)
    ensure_watchdog()
    return client


def failure_for(server: HTTPServer) -> Optional[str]:
    """The recorded reason this entry is not usable, if any."""
    with _LOCK:
        failed = _FAILED.get(server.key())
    if failed is None:
        return None
    return explain(_kind_of(failed[1]), redact(failed[1]))


def _kind_of(message: str) -> str:
    lowered = (message or "").lower()
    if "redirection" in lowered:
        return "unreachable"
    if "jeton" in lowered or "401" in lowered or "403" in lowered:
        return "auth"
    if "temps" in lowered or "timeout" in lowered:
        return "timeout"
    if "injoignable" in lowered:
        return "unreachable"
    if "protocole" in lowered or "json" in lowered:
        return "incompatible"
    return "unreachable"


#: ``MCPError.kind`` -> the state the panel shows. One table, so a new kind
#: cannot silently become "injoignable" in one code path and "incompatible" in
#: another.
KIND_TO_STATE = {
    "auth": "auth",
    "timeout": "timeout",
    "unreachable": "unreachable",
    "transport": "unreachable",
    "incompatible": "incompatible",
    "protocol": "incompatible",
    "server": "incompatible",
    "configuration": "invalid",
    "unavailable": "reconnecting",
}


def status() -> List[Dict[str, Any]]:
    """Every HTTP entry with the state this process can prove, no network call."""
    rows: List[Dict[str, Any]] = []
    for server in servers_from_config():
        key = server.key()
        with _LOCK:
            client = _CLIENTS.get(key)
            failed = _FAILED.get(key)
        if client is not None and client.alive:
            rows.append(client.status())
            continue
        state = "configured"
        reason = ""
        if failed is not None:
            state, reason = _kind_of(failed[1]), redact(failed[1])
        rows.append(
            {
                "name": server.name,
                "transport": server.transport,
                "url": server.url,
                "connected": False,
                "state": state,
                "label": LABELS.get(state, state),
                "tools": [],
                "reason": reason,
                "advice": explain(state, reason) if reason else "",
                "insecure": server.insecure,
            }
        )
    return rows


def _watch_once() -> None:
    """Probe every live client, reconnect the ones that dropped, honour backoff."""
    now = time.monotonic()
    with _LOCK:
        live = [(key, client) for key, client in _CLIENTS.items() if not client._closed]
        failed = dict(_FAILED)
    for key, client in live:
        if client.alive and client.probe():
            continue
        if client.backoff_due(failed.get(key), now):
            _reconnect(key, client.server)


def _reconnect(key: Tuple[Any, ...], server: HTTPServer) -> None:
    try:
        client_for(server, force=True)
    except MCPError:
        return


def compute_backoff(attempts: int) -> float:
    """1, 2, 4, ... capped: a server that comes back is noticed without a storm."""
    return float(min(BACKOFF_MAX, BACKOFF_START * (2 ** max(0, int(attempts) - 1))))


def ensure_watchdog() -> None:
    """Start the probe loop once per process."""
    global _WATCH
    if os.environ.get("UH_MCP_WATCH", "1") == "0":
        return
    with _LOCK:
        if _WATCH is not None and _WATCH.is_alive():
            return
        _WATCH_STOP.clear()
        _WATCH = threading.Thread(target=_watch_loop, name="mcp-http-watch", daemon=True)
        _WATCH.start()


def _watch_loop() -> None:
    while not _WATCH_STOP.wait(WATCH_INTERVAL):
        try:
            _watch_once()
        except Exception:  # noqa: BLE001 - a watchdog that dies is not a watchdog
            continue


def stop_watchdog() -> None:
    _WATCH_STOP.set()


def shutdown() -> None:
    """Stop every HTTP client and the watchdog. Never raises."""
    stop_watchdog()
    with _LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
        _FAILED.clear()
    for client in clients:
        client.close()


# --------------------------------------------------------------------------- #
# per-call approval
# --------------------------------------------------------------------------- #
class Ticket:
    """One question asked to the person in front of UH."""

    def __init__(self, server: str, tool: str, arguments: Dict[str, Any]) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.server = server
        self.tool = tool
        self.arguments = arguments
        self.answer: Optional[bool] = None
        self.created = time.time()
        self.done = threading.Event()

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "server": self.server,
            "tool": self.tool,
            "arguments": self.arguments,
            "asked": self.created,
        }


class ApprovalGate:
    """The per-call question a remote tool must answer before it runs.

    No registered notifier means no remote tool at all (see :func:`build_toolsets`):
    the design is fail-closed, so "the UI forgot to wire it" cannot become
    "the model runs a remote write on its own".
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: Dict[str, Ticket] = {}

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [ticket.public() for ticket in self._pending.values()]

    def resolve(self, ticket_id: str, allow: bool) -> bool:
        with self._lock:
            ticket = self._pending.get(ticket_id)
        if ticket is None:
            return False
        ticket.answer = bool(allow)
        ticket.done.set()
        return True

    def ask(
        self,
        server: str,
        tool: str,
        arguments: Dict[str, Any],
        *,
        notify: Callable[[Dict[str, Any]], None],
        timeout: float = APPROVAL_TIMEOUT,
    ) -> bool:
        ticket = Ticket(server, tool, arguments)
        with self._lock:
            self._pending[ticket.id] = ticket
        try:
            notify({"type": "approval", **ticket.public(), "timeout": timeout})
            if not ticket.done.wait(timeout):
                return False
            return bool(ticket.answer)
        finally:
            with self._lock:
                self._pending.pop(ticket.id, None)


GATE = ApprovalGate()


# --------------------------------------------------------------------------- #
# agent-loop integration
# --------------------------------------------------------------------------- #
class Toolset:
    """One HTTP/SSE MCP server, gated by a per-call approval."""

    def __init__(
        self,
        client: HTTPClient,
        *,
        notify: Callable[[Dict[str, Any]], None],
        gate: ApprovalGate = GATE,
    ) -> None:
        self.client = client
        self.notify = notify
        self.gate = gate
        self.name = f"mcp:{client.server.name}"
        self._by_exposed: Dict[str, str] = {}
        self._specs: List[Dict[str, Any]] = []
        taken: set[str] = set()
        for tool in client.tools:
            raw_name = str(tool.get("name"))
            exposed = exposed_name(client.server.name, raw_name)
            if exposed in taken:
                continue
            taken.add(exposed)
            self._by_exposed[exposed] = raw_name
            self._specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": exposed,
                        "description": str(
                            tool.get("description")
                            or f"{raw_name} (serveur MCP distant '{client.server.name}')"
                        )[:1024],
                        "parameters": tool.get("inputSchema")
                        if isinstance(tool.get("inputSchema"), dict)
                        else {"type": "object", "properties": {}},
                    },
                }
            )

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._specs

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raw = self._by_exposed.get(name)
        if raw is None:
            return {
                "error": f"unknown MCP tool '{name}'; known: "
                f"{', '.join(sorted(self._by_exposed)) or 'none'}",
                "kind": "invalid_request",
            }
        allowed = self.gate.ask(
            self.client.server.name,
            raw,
            dict(arguments or {}),
            notify=self.notify,
            timeout=APPROVAL_TIMEOUT,
        )
        if not allowed:
            return {
                "error": (
                    f"appel refusé : l'outil « {raw} » du serveur distant "
                    f"« {self.client.server.name} » n'a pas été autorisé. Rien n'a été exécuté."
                ),
                "kind": "approval",
                "server": self.client.server.name,
            }
        try:
            return self.client.call(raw, arguments or {})
        except MCPError as exc:
            return {
                "error": redact(str(exc)),
                "kind": exc.kind,
                "server": self.client.server.name,
            }


def build_toolsets(
    *,
    notify: Optional[Callable[[Dict[str, Any]], None]] = None,
    config: Optional[Config] = None,
) -> Dict[str, Toolset]:
    """One toolset per HTTP server that answers - and only with an approver.

    ``notify`` is how a tool call asks its question. The console has none, so it
    gets no remote tools: that is the prerequisite the security review asked for,
    not a limitation to work around.
    """
    if notify is None:
        return {}
    out: Dict[str, Toolset] = {}
    for server in servers_from_config(config):
        try:
            client = client_for(server)
        except MCPError:
            continue
        out[f"mcp:{server.name}"] = Toolset(client, notify=notify)
    return out


def test_connection(name: str, *, config: Optional[Config] = None) -> Dict[str, Any]:
    """Handshake, tool list, duration - what the button runs. Never cached."""
    server = next((item for item in servers_from_config(config) if item.name == name), None)
    if server is None:
        known = ", ".join(item.name for item in servers_from_config(config)) or "aucun"
        raise MCPError(
            f"aucun serveur HTTP nommé '{name}' (connus : {known})", kind="configuration"
        )
    started = time.monotonic()
    client = HTTPClient(server)
    keep = False
    try:
        client.start()
        keep = True
        elapsed = int((time.monotonic() - started) * 1000)
        return {
            **client.status(),
            "state": "connected",
            "label": LABELS["connected"],
            "duration_ms": elapsed,
            "reason": "",
            "advice": "",
            "warning": (
                "HTTP non chiffré : le jeton circule en clair sur le réseau."
                if server.insecure
                else ""
            ),
        }
    except MCPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        kind = KIND_TO_STATE.get(exc.kind) or _kind_of(str(exc))
        return {
            "name": server.name,
            "transport": server.transport,
            "url": server.url,
            "connected": False,
            "state": kind,
            "label": LABELS.get(kind, kind),
            "tools": [],
            "duration_ms": elapsed,
            "reason": redact(str(exc)),
            "advice": explain(kind, ""),
        }
    finally:
        if keep:
            # A successful test leaves the client attached: "connected" must mean
            # the next call has somewhere to go, not "it worked a second ago".
            with _LOCK:
                previous = _CLIENTS.pop(server.key(), None)
                _CLIENTS[server.key()] = client
                _FAILED.pop(server.key(), None)
            if previous is not None and previous is not client:
                previous.close()
            ensure_watchdog()
        else:
            client.close()


def probe(server: HTTPServer, *, timeout: float = 5.0) -> Dict[str, Any]:
    """Handshake against a candidate HTTP/SSE entry, writing nothing.

    The popup tests before it saves, so nothing here registers a client, keeps a
    session or touches ``config.toml``. The five-second cap is the promise of the
    button; a server that needs longer says so instead of freezing the dialog.
    """
    candidate = HTTPServer(
        name=server.name,
        url=server.url,
        transport=server.transport,
        token=server.token,
        timeout=timeout,
        allow=server.allow,
        insecure=server.insecure,
    )
    started = time.monotonic()
    client = HTTPClient(candidate)
    try:
        client.start(limit=timeout)
        elapsed = int((time.monotonic() - started) * 1000)
        return {
            **client.status(),
            "state": "connected",
            "label": LABELS["connected"],
            "duration_ms": elapsed,
            "reason": "",
            "advice": "",
            "warning": (
                "HTTP non chiffré : le jeton circule en clair sur le réseau."
                if server.insecure
                else ""
            ),
        }
    except MCPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        kind = KIND_TO_STATE.get(exc.kind) or _kind_of(str(exc))
        return {
            "name": server.name,
            "transport": client.transport,
            "url": server.url,
            "connected": False,
            "state": kind,
            "label": LABELS.get(kind, kind),
            "tools": [],
            "duration_ms": elapsed,
            "reason": redact(str(exc)),
            "advice": explain(kind, ""),
        }
    finally:
        client.close()


__all__ = [
    "ADVICE",
    "APPROVAL_TIMEOUT",
    "ApprovalGate",
    "ApprovalRequired",
    "GATE",
    "HTTPClient",
    "HTTPServer",
    "LABELS",
    "Toolset",
    "build_toolsets",
    "client_for",
    "probe",
    "compute_backoff",
    "ensure_watchdog",
    "explain",
    "servers_from_config",
    "shutdown",
    "status",
    "test_connection",
]
