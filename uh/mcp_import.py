"""Read a server configuration from JSON or YAML, with no new dependency.

Claude Desktop, VS Code and most MCP clients publish the same shape::

    {"mcpServers": {"atelier": {"command": "npx", "args": ["-y", "pkg"]}}}

This module accepts that, the same object without the wrapper, a bare list of
server objects, and a **small YAML subset**: maps, scalar lists, quoted strings
and comments. Nothing else - an anchor, a multi-line block or a tab-indented file
is refused with its line number instead of being half-read, because an import
that silently drops half a config is worse than one that says no.

A timeout or a `token` key may ride along; the caller decides where the secret
goes (``mcp-secrets.toml``), never this parser.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

MAX_BYTES = 64 * 1024
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class MCPImportError(RuntimeError):
    """The file is not a configuration UH can read. The message names the line."""


def _slug(name: str, index: int) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")
    value = value[:32]
    if not value or not value[0].isalnum():
        value = f"serveur-{index}"
    return value[:32] if _NAME.match(value) else f"serveur-{index}"


def _scalar(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    # A trailing comment is only stripped outside quotes, and YAML requires a
    # space before the '#': a URL fragment is not a comment.
    match = re.search(r"\s+#", value)
    if match:
        value = value[: match.start()].strip()
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _yaml_block(lines: List[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if lines[index][1].strip().startswith("- "):
        items: List[str] = []
        while index < len(lines) and _indent(lines[index][1]) == indent:
            text = lines[index][1].strip()
            if not text.startswith("- "):
                break
            item = text[2:].strip()
            if ":" in item and not item.startswith(("http://", "https://")):
                raise MCPImportError(
                    f"ligne {lines[index][0]}: une liste de tables n'est pas prise en charge"
                )
            items.append(_scalar(item))
            index += 1
        return items, index

    table: Dict[str, Any] = {}
    while index < len(lines):
        number, raw = lines[index]
        current = _indent(raw)
        if current < indent:
            break
        text = raw.strip()
        if current > indent:
            raise MCPImportError(f"ligne {number}: indentation inattendue")
        if text.startswith("- "):
            break
        if "\t" in raw:
            raise MCPImportError(f"ligne {number}: les tabulations sont refusées, utilise des espaces")
        key, separator, rest = text.partition(":")
        if not separator or not key.strip():
            raise MCPImportError(f"ligne {number}: 'clé: valeur' attendu")
        name = _scalar(key.strip())
        rest = rest.strip()
        if rest:
            table[name] = _scalar(rest)
            index += 1
            continue
        index += 1
        if index < len(lines) and _indent(lines[index][1]) > current:
            value, index = _yaml_block(lines, index, _indent(lines[index][1]))
            table[name] = value
        else:
            table[name] = {}
    return table, index


def _yaml(text: str) -> Any:
    lines = [
        (number, raw.rstrip())
        for number, raw in enumerate(text.splitlines(), 1)
        if raw.strip() and not raw.lstrip().startswith("#")
    ]
    if not lines:
        raise MCPImportError("fichier vide")
    value, index = _yaml_block(lines, 0, _indent(lines[0][1]))
    if index != len(lines):
        raise MCPImportError(f"ligne {lines[index][0]}: structure non comprise")
    return value


def _load(text: str, filename: str = "") -> Any:
    raw = (text or "").lstrip("\ufeff").strip()
    if not raw:
        raise MCPImportError("fichier vide")
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise MCPImportError(f"fichier trop gros (limite {MAX_BYTES // 1024} Ko)")
    lower = (filename or "").lower()
    looks_json = raw.startswith(("{", "["))
    if lower.endswith(".json") or looks_json:
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise MCPImportError(f"JSON invalide: {exc}") from exc
    return _yaml(raw)


def _looks_like_server(entry: Dict[str, Any]) -> bool:
    return any(key in entry for key in ("command", "url", "args", "env", "transport"))


def _one(name: str, entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        raise MCPImportError(f"{name}: un serveur doit être une table")
    transport = str(entry.get("transport") or "").strip().lower()
    url = str(entry.get("url") or entry.get("endpoint") or "").strip()
    command = str(entry.get("command") or "").strip()
    if transport not in {"stdio", "http", "sse"}:
        transport = "http" if url else "stdio"

    args_raw = entry.get("args") or []
    if isinstance(args_raw, str):
        args = [part.strip() for part in args_raw.splitlines() if part.strip()]
    elif isinstance(args_raw, list):
        args = [str(item) for item in args_raw]
    else:
        raise MCPImportError(f"{name}: 'args' doit être une liste")

    env_raw = entry.get("env") or {}
    if not isinstance(env_raw, dict):
        raise MCPImportError(f"{name}: 'env' doit être une table")
    env = {str(key): str(value) for key, value in env_raw.items() if str(key).strip()}

    token = str(entry.get("token") or entry.get("bearer") or "").strip()
    headers = entry.get("headers")
    if not token and isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == "authorization":
                token = str(value).replace("Bearer ", "").strip()

    server: Dict[str, Any] = {"name": _slug(name, index), "transport": transport}
    if transport == "stdio":
        if not command:
            raise MCPImportError(f"{name}: 'command' est requis pour un transport stdio")
        server["command"] = command
        if args:
            server["args"] = args
        if env:
            server["env"] = env
    else:
        if not url:
            raise MCPImportError(f"{name}: 'url' est requise pour un transport {transport}")
        server["url"] = url
        if token:
            server["token"] = token
    return server


def servers(payload: Any) -> List[Dict[str, Any]]:
    """Normalise whatever the file held into a list of server entries."""
    if isinstance(payload, dict) and isinstance(payload.get("mcpServers"), dict):
        payload = payload["mcpServers"]
    elif isinstance(payload, dict) and isinstance(payload.get("servers"), (dict, list)):
        payload = payload["servers"]

    pairs: List[tuple[str, Any]] = []
    if isinstance(payload, list):
        pairs = [(str(item.get("name") or f"serveur-{index + 1}"), item) for index, item in enumerate(payload)]
    elif isinstance(payload, dict):
        if _looks_like_server(payload):
            pairs = [(str(payload.get("name") or "serveur"), payload)]
        else:
            pairs = [(str(name), entry) for name, entry in payload.items() if isinstance(entry, dict)]
    if not pairs:
        raise MCPImportError("aucun serveur trouvé dans le fichier")
    if len(pairs) > 20:
        raise MCPImportError("plus de 20 serveurs dans un seul fichier: importe-les par lots")

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, (name, entry) in enumerate(pairs, start=1):
        server = _one(name, entry, index)
        if server["name"] in seen:
            raise MCPImportError(f"deux serveurs portent le nom '{server['name']}'")
        seen.add(server["name"])
        out.append(server)
    return out


def parse(text: str, filename: str = "") -> List[Dict[str, Any]]:
    """Text of a JSON/YAML file -> normalised server entries."""
    return servers(_load(text, filename))


__all__ = ["MAX_BYTES", "MCPImportError", "parse", "servers"]
