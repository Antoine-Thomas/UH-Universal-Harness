"""MCP client (stdio): the tools UH does not ship.

The Model Context Protocol is a JSON-RPC 2.0 conversation with a child process:
``initialize`` -> ``notifications/initialized`` -> ``tools/list`` ->
``tools/call``. UH speaks the stdio transport, where one JSON message is one
line of the child's stdout - the same shape Claude Desktop and every published
server already implement, so ``npx @modelcontextprotocol/server-filesystem`` is
a configuration line, not a port.

Why a client and not a plugin API: the gap it closes is *external* tools. A
filesystem server, a SQLite server, a browser server - each is somebody else's
process, with its own permissions, that UH must not reimplement. The tool names
the model sees are namespaced ``mcp__<server>__<tool>``, so two servers may both
expose ``read_file`` without ambiguity, and a name in a transcript always says
which process answered.

Fail-closed, in the same spirit as the rest of UH:

* the process is only started because ``config.toml`` asks for it - a model can
  never install, spawn or enable a server;
* an optional ``allow`` list per server filters ``tools/list``; a tool that is
  not named there is not advertised at all, so the model cannot even see it;
* a response larger than :data:`MAX_MESSAGE_BYTES` is refused, not buffered: a
  runaway server cannot take the harness down with it;
* a tool result is truncated at :data:`MAX_RESULT_CHARS` with an explicit
  marker, because the model's context is the real budget;
* every failure - a server that never answers, dies mid-call, or returns an
  error - becomes ``{"error": ..., "kind": ...}``, a tool result the model can
  read. Nothing raises out of :meth:`Toolset.call`, so a broken server costs one
  turn, never a session;
* a failed start is remembered for :data:`RETRY_AFTER` seconds: the cockpit
  polls ``/api/tools/status``, and a dead server must not mean a 20 s timeout on
  every poll.

Configuration (hand-written, never written by ``uh config set``)::

    [[mcp.servers]]
    name = "filesystem"
    command = "npx"
    args = ["-y", "@modelcontextprotocol/server-filesystem", "C:/Users/me/notes"]
    # optional
    timeout = 30          # seconds, per request
    allow = ["read_file", "list_directory"]
    cwd = "C:/Users/me"
    env = { LOG_LEVEL = "error" }

``[mcp]`` is a read-only table for the same reason ``[providers.file]`` is:
UH's serialiser is flat, and a writer that silently dropped a server list would
be worse than one that refuses.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .config import load as load_config

#: The revision UH implements. Servers answering a different one are accepted -
#: the handshake is a negotiation, and every published version so far keeps the
#: three methods below identical.
PROTOCOL_VERSION = "2024-11-05"

#: One response line, hard cap. Ten megabytes is already two orders of magnitude
#: more than a tool result should be; beyond it the server is broken, not busy.
MAX_MESSAGE_BYTES = 10 * 1024 * 1024

#: What the model actually receives from one tool call. A filesystem server can
#: return a whole file; the context window is the budget that matters.
MAX_RESULT_CHARS = 200_000

#: Per-request timeout when the configuration says nothing.
DEFAULT_TIMEOUT = 30.0

#: ``initialize`` is a process launch (``npx`` may download on first run), so it
#: gets more room than a tool call but less than "forever".
START_TIMEOUT = 20.0

#: How long a failed server stays failed. The cockpit polls the status endpoint;
#: without this, one dead server means one timeout per poll.
RETRY_AFTER = 30.0

#: Kept from the child's stderr so a crash can quote the server's own words.
STDERR_TAIL = 4096

PREFIX = "mcp"
SEPARATOR = "__"
MAX_TOOL_NAME = 64

_NAME_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class MCPError(RuntimeError):
    """A server-level failure, with a ``kind`` the caller can act on.

    ``kind`` is one of ``configuration`` (the entry in config.toml is unusable),
    ``transport`` (the process is gone), ``timeout`` (it did not answer),
    ``protocol`` (it answered something that is not JSON-RPC), ``server`` (it
    returned a JSON-RPC error) or ``unavailable`` (a recent start failed and the
    backoff is still running).
    """

    def __init__(self, message: str, kind: str = "transport", **details: Any) -> None:
        super().__init__(message)
        self.kind = kind
        self.details = details


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ServerConfig:
    """One ``[[mcp.servers]]`` entry, validated."""

    name: str
    command: str
    args: Tuple[str, ...] = ()
    cwd: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    allow: Optional[Tuple[str, ...]] = None
    timeout: float = DEFAULT_TIMEOUT

    def key(self) -> Tuple[Any, ...]:
        """Identity for the process cache: same entry, same process."""
        return (
            self.name,
            self.command,
            self.args,
            self.cwd,
            tuple(sorted(self.env.items())),
        )


def _as_text_list(raw: Any) -> Optional[List[str]]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    values = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return values


def _parse_server(raw: Any, index: int) -> ServerConfig:
    """One entry -> :class:`ServerConfig`, or :class:`MCPError` (configuration)."""
    where = f"mcp.servers[{index}]"
    if not isinstance(raw, dict):
        raise MCPError(f"{where}: expected a table", kind="configuration")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MCPError(f"{where}: 'name' is required and must be a string", kind="configuration")
    name = name.strip()
    if _NAME_SAFE.sub("_", name) != name:
        raise MCPError(
            f"{where}: name '{name}' may only contain letters, digits, '_' and '-' "
            "(it is part of every tool name the model sees)",
            kind="configuration",
        )
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip():
        raise MCPError(f"mcp.servers[{name}]: 'command' is required", kind="configuration")

    args = _as_text_list(raw.get("args", []))
    if args is None:
        raise MCPError(f"mcp.servers[{name}]: 'args' must be a list of strings",
                       kind="configuration")
    allow_raw = raw.get("allow")
    allow = _as_text_list(allow_raw) if allow_raw is not None else None
    if allow_raw is not None and allow is None:
        raise MCPError(f"mcp.servers[{name}]: 'allow' must be a list of strings",
                       kind="configuration")

    cwd = raw.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise MCPError(f"mcp.servers[{name}]: 'cwd' must be a string", kind="configuration")

    env_raw = raw.get("env", {})
    if not isinstance(env_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env_raw.items()
    ):
        raise MCPError(f"mcp.servers[{name}]: 'env' must be a table of strings",
                       kind="configuration")

    timeout_raw = raw.get("timeout", DEFAULT_TIMEOUT)
    if isinstance(timeout_raw, bool) or not isinstance(timeout_raw, (int, float)):
        raise MCPError(f"mcp.servers[{name}]: 'timeout' must be a number of seconds",
                       kind="configuration")
    if timeout_raw <= 0:
        raise MCPError(f"mcp.servers[{name}]: 'timeout' must be positive", kind="configuration")

    return ServerConfig(
        name=name,
        command=command.strip(),
        args=tuple(args),
        cwd=cwd,
        env={**dict(env_raw), **secrets_for(name)},
        allow=tuple(allow) if allow is not None else None,
        timeout=float(timeout_raw),
    )


def secrets_for(name: str) -> Dict[str, str]:
    """Environment values kept out of ``config.toml`` (see ``uh.mcp_cloud``).

    A token in ``config.toml`` ends up in a screenshot, a backup or an issue; the
    same token in ``mcp-secrets.toml`` (0600) does not. A broken secrets file is
    an empty dict, never a failed start: the server then fails on its own terms.
    """
    try:
        from .mcp_cloud import read_secrets

        return dict(read_secrets().get(name, {}))
    except Exception:  # noqa: BLE001 - never let a secret file break a spawn
        return {}


def servers_from_config(config: Optional[Config] = None) -> Tuple[List[ServerConfig], List[str]]:
    """Every usable server, plus a sentence per entry that is not usable.

    A malformed entry never hides the others: the cockpit shows what it skipped
    and why, which is the difference between "no tools" and "one typo on line 4".
    """
    cfg = config if config is not None else load_config()
    section = cfg.tables.get("mcp") or {}
    raw_servers = section.get("servers") if isinstance(section, dict) else None
    if raw_servers is None:
        return [], []
    if not isinstance(raw_servers, list):
        return [], ["mcp.servers must be an array of tables ([[mcp.servers]])"]

    servers: List[ServerConfig] = []
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for index, raw in enumerate(raw_servers):
        # An HTTP/SSE entry is a decision the user made, not a mistake: this client
        # speaks stdio, so it is skipped without a warning. `uh.mcp_cloud.
        # pending_http_servers` reads those back for the panel.
        if isinstance(raw, dict) and str(raw.get("transport") or "").lower() in (
            "http",
            "sse",
        ):
            continue
        try:
            server = _parse_server(raw, index)
        except MCPError as exc:
            problems.append(str(exc))
            continue
        if server.name in seen:
            problems.append(
                f"mcp.servers[{index}]: duplicate name '{server.name}' - ignored "
                f"(already defined by mcp.servers[{seen[server.name]}])"
            )
            continue
        seen[server.name] = index
        servers.append(server)
    return servers, problems


# --------------------------------------------------------------------------- #
# one server
# --------------------------------------------------------------------------- #
def _clean(text: str) -> str:
    """Tool-name characters only: the model's function-calling schemas are strict."""
    return _NAME_SAFE.sub("_", text).strip("_") or "tool"


def exposed_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``, capped at the 64 characters providers accept."""
    name = f"{PREFIX}{SEPARATOR}{_clean(server)}{SEPARATOR}{_clean(tool)}"
    if len(name) <= MAX_TOOL_NAME:
        return name
    return name[:MAX_TOOL_NAME]


def _truncate(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    dropped = len(text) - MAX_RESULT_CHARS
    return text[:MAX_RESULT_CHARS] + f"\n[truncated by UH: {dropped} more characters]"


def _render(result: Dict[str, Any]) -> Tuple[str, Optional[Any]]:
    """MCP ``content`` -> the text the model reads.

    Image and audio blocks are described, never inlined: a base64 PNG is tens of
    thousands of tokens that mean nothing to a text model.
    """
    parts: List[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            kind = item.get("type")
            if kind == "text":
                parts.append(str(item.get("text") or ""))
            elif kind in {"image", "audio"}:
                data = item.get("data") or ""
                parts.append(f"[{kind}: {item.get('mimeType') or 'unknown'}, "
                             f"{len(str(data))} base64 characters]")
            elif kind == "resource":
                resource = item.get("resource") or {}
                uri = resource.get("uri") if isinstance(resource, dict) else None
                parts.append(f"[resource: {uri or 'unnamed'}]")
            else:
                parts.append(f"[{kind or 'unknown content'}]")
    elif content is not None:
        parts.append(str(content))
    if not parts and result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, default=str))
    return _truncate("\n".join(part for part in parts if part)), result.get("structuredContent")


class Client:
    """One MCP server process, and the request/response loop in front of it.

    One request in flight per server: MCP servers are usually single-threaded
    scripts, and a harness that pipelines into them gains nothing but ordering
    bugs. The lock is what makes the reader thread's single queue unambiguous.
    """

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.server_info: Dict[str, Any] = {}
        self.protocol_version: str = ""
        self.tools: List[Dict[str, Any]] = []
        self._process: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[Any]]" = queue.Queue()
        self._request_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stderr = bytearray()
        self._noise: List[str] = []
        self._next_id = 0
        self._dead = False
        self._exit_code: Optional[int] = None

    # -- lifecycle --------------------------------------------------------- #
    @property
    def alive(self) -> bool:
        return not self._dead and self._process is not None and self._process.poll() is None

    def start(self, *, limit: float | None = None) -> "Client":
        """Start the process and handshake. ``limit`` caps the initialize wait.

        A probe from the cockpit promises an answer in five seconds; the default
        (START_TIMEOUT) stays generous for a cold ``npx`` on a first run.
        """
        if self.alive:
            return self
        argv = [self._resolve_command(), *self.config.args]
        env = dict(os.environ)
        env.update(self.config.env)
        kwargs: Dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": self.config.cwd,
            "env": env,
            "bufsize": 0,
            "shell": False,
            "close_fds": True,
        }
        if sys.platform.startswith("win"):
            # No console window: a shortcut that opens an app window must not
            # flash a black box for every server it starts.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = subprocess.Popen(argv, **kwargs)  # noqa: S603 - the user's config
        except (OSError, ValueError) as exc:
            raise MCPError(
                f"MCP server '{self.config.name}': cannot start {self.config.command!r} "
                f"({exc})",
                kind="configuration",
            ) from exc

        self._dead = False
        self._exit_code = None
        for target in (self._read_stdout, self._read_stderr):
            threading.Thread(target=target, name=f"mcp-{self.config.name}", daemon=True).start()

        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "uh", "version": _version()},
            },
            timeout=limit if limit is not None else max(self.config.timeout, START_TIMEOUT),
        )
        self.protocol_version = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        self._notify("notifications/initialized")
        self.tools = self._list_tools()
        return self

    def close(self) -> None:
        """Stop the process. Never raises: shutting down is not a failure path."""
        process = self._process
        self._dead = True
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._process = None

    def _resolve_command(self) -> str:
        """``npx`` is ``npx.cmd`` on Windows: PATH lookup must honour PATHEXT."""
        found = shutil.which(self.config.command)
        return found or self.config.command

    # -- pumps ------------------------------------------------------------- #
    def _read_stdout(self) -> None:
        stream = self._process.stdout if self._process else None
        if stream is None:  # pragma: no cover - start() guarantees a pipe
            self._lines.put(None)
            return
        while True:
            try:
                raw = stream.readline(MAX_MESSAGE_BYTES + 1)
            except (OSError, ValueError):  # pipe closed under us
                break
            if not raw:
                break
            if len(raw) == MAX_MESSAGE_BYTES + 1 and not raw.endswith(b"\n"):
                # Refuse, then drain to the end of the line: leaving the rest in
                # the pipe would desynchronise every later message.
                dropped = len(raw)
                while True:
                    chunk = stream.readline(MAX_MESSAGE_BYTES)
                    if not chunk or chunk.endswith(b"\n"):
                        dropped += len(chunk)
                        break
                    dropped += len(chunk)
                self._lines.put(MCPError(
                    f"MCP server '{self.config.name}' answered {dropped} bytes for one "
                    f"message (limit {MAX_MESSAGE_BYTES})",
                    kind="protocol",
                ))
                continue
            line = raw.strip()
            if line:
                self._lines.put(line)
        self._dead = True
        self._lines.put(None)

    def _read_stderr(self) -> None:
        stream = self._process.stderr if self._process else None
        if stream is None:  # pragma: no cover
            return
        while True:
            try:
                chunk = stream.readline(4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            self._stderr.extend(chunk)
            if len(self._stderr) > STDERR_TAIL:
                del self._stderr[: len(self._stderr) - STDERR_TAIL]

    def stderr_tail(self) -> str:
        """The server's own words, with any stored secret replaced by its mask.

        A server that dumps its environment on start must not put the token in an
        error message that the cockpit renders and the user pastes into a ticket.
        """
        raw = bytes(self._stderr).decode("utf-8", "replace").strip()
        try:
            from .mcp_cloud import redact

            return redact(raw)
        except Exception:  # noqa: BLE001 - redaction is never worth failing a call
            return ""

    # -- json-rpc ---------------------------------------------------------- #
    def _write(self, payload: Dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPError(f"MCP server '{self.config.name}' is not running", kind="transport")
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._write_lock:
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (OSError, ValueError) as exc:
                self._dead = True
                raise MCPError(
                    f"MCP server '{self.config.name}' closed its input ({exc})",
                    kind="transport",
                    stderr=self.stderr_tail(),
                ) from exc

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def _request(
        self, method: str, params: Optional[Dict[str, Any]] = None, *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        limit = timeout if timeout is not None else self.config.timeout
        with self._request_lock:
            self._next_id += 1
            request_id = self._next_id
            payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
            if params is not None:
                payload["params"] = params
            self._write(payload)
            return self._await(request_id, method, limit)

    def _await(self, request_id: int, method: str, limit: float) -> Dict[str, Any]:
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(
                    f"MCP server '{self.config.name}' did not answer {method} within "
                    f"{limit:.0f}s",
                    kind="timeout",
                    stderr=self.stderr_tail(),
                )
            try:
                item = self._lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if item is None:
                code = None
                if self._process is not None:
                    try:
                        code = self._process.wait(timeout=2)
                    except subprocess.TimeoutExpired:  # pragma: no cover - EOF came first
                        code = self._process.poll()
                raise MCPError(
                    f"MCP server '{self.config.name}' exited"
                    + (f" with code {code}" if code is not None else "")
                    + f" during {method}",
                    kind="transport",
                    stderr=self.stderr_tail(),
                )
            if isinstance(item, MCPError):
                raise item
            try:
                message = json.loads(item)
            except (ValueError, UnicodeDecodeError):
                # A server that prints a banner instead of JSON is broken, but the
                # line is quoted in the timeout message rather than swallowed.
                self._noise.append(item.decode("utf-8", "replace")[:200])
                del self._noise[:-5]
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                continue  # a notification, or a late answer to a timed-out request
            if "error" in message:
                error = message.get("error") or {}
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise MCPError(
                    f"MCP server '{self.config.name}' refused {method}: {detail or error}",
                    kind="server",
                    code=error.get("code") if isinstance(error, dict) else None,
                )
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    # -- protocol surface -------------------------------------------------- #
    def _list_tools(self) -> List[Dict[str, Any]]:
        result = self._request("tools/list", {})
        raw = result.get("tools")
        tools = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
        if self.config.allow is not None:
            allowed = set(self.config.allow)
            tools = [item for item in tools if str(item.get("name") or "") in allowed]
        return [item for item in tools if item.get("name")]

    def call(self, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """``tools/call`` -> a toolset-shaped payload. Raises only :class:`MCPError`."""
        result = self._request("tools/call", {"name": tool, "arguments": arguments or {}})
        text, structured = _render(result)
        payload: Dict[str, Any] = {
            "server": self.config.name,
            "tool": tool,
            "text": text,
        }
        if structured is not None:
            payload["structured"] = structured
        if result.get("isError"):
            return {**payload, "error": text or "the MCP server reported an error",
                    "kind": "tool"}
        return payload

    def status(self) -> Dict[str, Any]:
        """What this client can prove right now. No ping: last known state only."""
        return {
            "name": self.config.name,
            "command": self.config.command,
            "connected": self.alive,
            "protocol": self.protocol_version,
            "server": self.server_info.get("name") or "",
            "version": self.server_info.get("version") or "",
            "tools": [str(tool.get("name")) for tool in self.tools],
        }


def _version() -> str:
    try:
        from . import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - a checkout always has one
        return "0"


# --------------------------------------------------------------------------- #
# process registry
# --------------------------------------------------------------------------- #
_CLIENTS: Dict[Tuple[Any, ...], Client] = {}
_FAILED: Dict[Tuple[Any, ...], Tuple[float, str]] = {}
_REGISTRY_LOCK = threading.Lock()


def client_for(config: ServerConfig) -> Client:
    """The live client for this entry, starting it once. Raises :class:`MCPError`."""
    key = config.key()
    with _REGISTRY_LOCK:
        existing = _CLIENTS.get(key)
        if existing is not None:
            if existing.alive:
                return existing
            existing.close()
            _CLIENTS.pop(key, None)

        failed = _FAILED.get(key)
        if failed is not None:
            age = time.monotonic() - failed[0]
            if age < RETRY_AFTER:
                raise MCPError(
                    f"MCP server '{config.name}' is not available "
                    f"(retrying in {RETRY_AFTER - age:.0f}s): {failed[1]}",
                    kind="unavailable",
                )
            _FAILED.pop(key, None)

        client = Client(config)
        try:
            client.start()
        except MCPError as exc:
            client.close()
            _FAILED[key] = (time.monotonic(), str(exc))
            raise
        _CLIENTS[key] = client
        return client


def shutdown() -> None:
    """Stop every server this process started. Never raises."""
    with _REGISTRY_LOCK:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
        _FAILED.clear()
    for client in clients:
        client.close()


atexit.register(shutdown)


# --------------------------------------------------------------------------- #
# agent-loop integration
# --------------------------------------------------------------------------- #
class Toolset:
    """One MCP server, as a UH toolset (``name``, ``tools``, ``call``)."""

    def __init__(self, client: Client) -> None:
        self.client = client
        self.name = f"{PREFIX}:{client.config.name}"
        self._by_exposed: Dict[str, str] = {}
        self._specs: List[Dict[str, Any]] = []
        taken: set[str] = set()
        for tool in client.tools:
            raw_name = str(tool.get("name"))
            exposed = exposed_name(client.config.name, raw_name)
            if exposed in taken:
                continue  # two tools whose names collapse to one: the first wins
            taken.add(exposed)
            self._by_exposed[exposed] = raw_name
            self._specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": exposed,
                        "description": str(
                            tool.get("description")
                            or f"{raw_name} (MCP server '{client.config.name}')"
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
        try:
            return self.client.call(raw, arguments or {})
        except MCPError as exc:
            return {"error": str(exc), "kind": exc.kind, "server": self.client.config.name}


def build_toolsets(config: Optional[Config] = None) -> Dict[str, Toolset]:
    """One toolset per server that answers. A server that does not, contributes none.

    Same rule as UHB: a tool that fails on every call is worse than an absent
    one. The failure is kept in :func:`status` (and in the backoff above) so the
    cockpit can say why instead of just showing nothing.
    """
    servers, _ = servers_from_config(config)
    toolsets: Dict[str, Toolset] = {}
    for server in servers:
        try:
            client = client_for(server)
        except MCPError:
            continue
        toolsets[f"{PREFIX}:{server.name}"] = Toolset(client)
    return toolsets


def redact(text: str) -> str:
    """Hide stored secret values before a message leaves the module."""
    try:
        from .mcp_cloud import redact as _redact

        return _redact(text)
    except Exception:  # noqa: BLE001 - redaction must never break a status call
        return ""


def status(config: Optional[Config] = None) -> Dict[str, Any]:
    """The MCP block of ``/api/tools/status``. Reports only what is known.

    No connection is attempted here: this runs on every cockpit poll, and a
    status endpoint that starts ``npx`` is a status endpoint that hangs.
    """
    servers, problems = servers_from_config(config)
    entries: List[Dict[str, Any]] = []
    for server in servers:
        key = server.key()
        client = _CLIENTS.get(key)
        if client is not None and client.alive:
            entries.append({**client.status(), "state": "connected"})
            continue
        entry: Dict[str, Any] = {
            "name": server.name,
            "command": server.command,
            "connected": False,
            "state": "failed" if key in _FAILED else "idle",
            "tools": [],
        }
        failed = _FAILED.get(key)
        if failed is not None:
            entry["reason"] = redact(failed[1])
        else:
            entry["reason"] = "not started yet: it starts on the first tool call, or on test"
        entries.append(entry)

    payload: Dict[str, Any] = {
        "supported": True,
        "configured": len(servers),
        "servers": entries,
    }
    if problems:
        payload["problems"] = problems
    if not servers and not problems:
        payload["reason"] = (
            "no [[mcp.servers]] in config.toml: nothing to attach yet"
            " (see docs/MCP.md and docs/ROADMAP_UH_V2.md)"
        )
    return payload


def test_server(name: str, config: Optional[Config] = None) -> Dict[str, Any]:
    """Connect, list the tools, disconnect. What the cockpit's "test" button runs.

    Deliberately not cached: a test that answers from a stale cache proves
    nothing, and the user pressed the button to ask the server a question now.
    """
    servers, _ = servers_from_config(config)
    server = next((item for item in servers if item.name == name), None)
    if server is None:
        raise MCPError(f"no MCP server named '{name}' in config.toml", kind="configuration")
    client = Client(server)
    try:
        client.start()
        return {**client.status(), "state": "connected"}
    finally:
        client.close()


#: The five states the cockpit shows for a candidate entry, in the vocabulary the
#: HTTP transport already uses (one table, so a probe and a saved server agree).
PROBE_LABELS = {
    "connected": "connecté",
    "auth": "jeton refusé",
    "unreachable": "injoignable",
    "incompatible": "protocole incompatible",
    "timeout": "délai dépassé",
    "invalid": "configuration refusée",
}

_PROBE_STATES = {
    "auth": "auth",
    "timeout": "timeout",
    "configuration": "invalid",
    "protocol": "incompatible",
    "server": "incompatible",
    "transport": "unreachable",
    "unavailable": "unreachable",
}


def probe_server(config: ServerConfig, *, timeout: float = 5.0) -> Dict[str, Any]:
    """Handshake against a candidate stdio entry, writing nothing.

    The cockpit's "+ Connecter un MCP" popup tests before it saves: this spawns
    the command with the caller's own config (same rules as ``config.toml``),
    keeps the whole exchange under ``timeout`` seconds, and never touches the
    registry or the disk.
    """
    started = time.monotonic()
    client = Client(config)
    try:
        client.start(limit=timeout)
        elapsed = int((time.monotonic() - started) * 1000)
        return {
            **client.status(),
            "state": "connected",
            "label": PROBE_LABELS["connected"],
            "duration_ms": elapsed,
            "reason": "",
            "advice": "",
        }
    except MCPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        state = _PROBE_STATES.get(exc.kind, "unreachable")
        if state == "timeout":
            advice = (
                f"Pas de réponse en {timeout:.0f} s. Un npx qui télécharge son paquet "
                "au premier lancement peut dépasser ce délai : relance le test."
            )
        elif state == "invalid":
            advice = "Vérifie la commande (npx, uvx, un .exe) et qu'elle est dans le PATH."
        else:
            advice = "Le programme a démarré mais n'a pas répondu comme un serveur MCP."
        return {
            "name": config.name,
            "command": config.command,
            "connected": False,
            "state": state,
            "label": PROBE_LABELS.get(state, state),
            "tools": [],
            "duration_ms": elapsed,
            "reason": redact(str(exc)),
            "advice": advice,
        }
    finally:
        client.close()
