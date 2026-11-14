"""Cloud connections for people who do not write TOML.

The MCP client is the mechanism; this module is the shop window. A template names
a real package, the fields a server needs, and - when the server can write - the
one tool that stores a file. Everything here was probed against the published
package before being written down: a template that installs nothing is worse than
no template.

Secrets never travel through ``config.toml``: they land in
``$UH_HOME/mcp-secrets.toml`` and are merged into the child's environment at spawn
(see :func:`uh.mcp.secrets_for`). ``config.toml`` is the file people paste into
issues and copy between machines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import tomllib

from .config import uh_home

SECRETS_FILE = "mcp-secrets.toml"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class CloudError(RuntimeError):
    """A template, a field or the config file is not usable. Always readable."""


@dataclass(frozen=True)
class Field:
    """One value a server needs. ``secret`` decides *where* it is stored."""

    key: str
    label: str
    secret: bool = False
    required: bool = True
    placeholder: str = ""
    help: str = ""


@dataclass(frozen=True)
class Template:
    ident: str
    label: str
    command: str
    args: tuple[str, ...]
    fields: tuple[Field, ...]
    what: str
    allow: Optional[tuple[str, ...]] = None
    upload_tool: str = ""
    upload_note: str = ""
    docs: str = ""

    def public(self) -> Dict[str, Any]:
        """What the UI is allowed to see: never a stored value."""
        return {
            "id": self.ident,
            "label": self.label,
            "what": self.what,
            "can_upload": bool(self.upload_tool),
            "upload_note": self.upload_note,
            "docs": self.docs,
            "fields": [
                {
                    "key": item.key,
                    "label": item.label,
                    "secret": item.secret,
                    "required": item.required,
                    "placeholder": item.placeholder,
                    "help": item.help,
                }
                for item in self.fields
            ],
        }


#: Probed with ``npx -y <package>`` and a real ``tools/list`` before being written.
TEMPLATES: Dict[str, Template] = {
    "s3": Template(
        ident="s3",
        label="AWS S3",
        command="npx",
        args=("-y", "mcp-server-s3"),
        what="Trier des documents et les déposer dans un bucket S3.",
        fields=(
            Field("AWS_ACCESS_KEY_ID", "Access key id", secret=True),
            Field("AWS_SECRET_ACCESS_KEY", "Secret access key", secret=True),
            Field("AWS_REGION", "Région", placeholder="eu-west-3"),
            Field(
                "S3_BUCKET",
                "Bucket par défaut",
                required=False,
                placeholder="mon-bucket",
                help="Utilisé par « Exaucer et uploader » quand aucun bucket n'est donné.",
            ),
        ),
        allow=(
            "list_buckets",
            "list_objects",
            "get_object",
            "put_object",
            "delete_object",
            "presigned_url",
            "bucket_info",
        ),
        upload_tool="put_object",
        upload_note="Écrit via put_object(bucket, key, content).",
        docs="https://www.npmjs.com/package/mcp-server-s3",
    ),
    "gdrive": Template(
        ident="gdrive",
        label="Google Drive (lecture)",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-gdrive"),
        what="Chercher et lire des documents déjà dans Drive. Pas d'écriture.",
        fields=(
            Field(
                "GDRIVE_OAUTH_PATH",
                "Client OAuth (JSON)",
                placeholder="C:/Users/moi/.gdrive/gcp-oauth.keys.json",
            ),
            Field(
                "GDRIVE_CREDENTIALS_PATH",
                "Credentials (token)",
                placeholder="C:/Users/moi/.gdrive/credentials.json",
            ),
        ),
        upload_note="Ce serveur ne publie pas d'outil d'écriture : la lecture seule est ce qu'il offre.",
        docs="https://www.npmjs.com/package/@modelcontextprotocol/server-gdrive",
    ),
}


def template_list() -> List[Dict[str, Any]]:
    return [item.public() for item in TEMPLATES.values()]


def template(ident: str) -> Template:
    found = TEMPLATES.get((ident or "").strip().lower())
    if found is None:
        raise CloudError(
            f"unknown template '{ident}'; known: {', '.join(sorted(TEMPLATES))}"
        )
    return found


def check_name(name: str) -> str:
    value = (name or "").strip().lower()
    if not _NAME.match(value):
        raise CloudError(
            "name must be lowercase letters, digits, '-' or '_' (max 32)"
        )
    return value


def build(
    ident: str, name: str, fields: Dict[str, str]
) -> tuple[Dict[str, Any], Dict[str, str]]:
    """Validate a form into a server entry plus its secret half."""
    spec = template(ident)
    entry_name = check_name(name)
    values = {str(key): str(value).strip() for key, value in (fields or {}).items()}

    missing = [
        item.key
        for item in spec.fields
        if item.required and not values.get(item.key, "").strip()
    ]
    if missing:
        raise CloudError("missing value(s): " + ", ".join(missing))
    unknown = sorted(set(values) - {item.key for item in spec.fields})
    if unknown:
        raise CloudError("unexpected field(s): " + ", ".join(unknown))

    public = {
        item.key: values[item.key]
        for item in spec.fields
        if not item.secret and values.get(item.key)
    }
    secret = {
        item.key: values[item.key]
        for item in spec.fields
        if item.secret and values.get(item.key)
    }
    entry: Dict[str, Any] = {
        "name": entry_name,
        "command": spec.command,
        "args": list(spec.args),
        "timeout": 60,
    }
    if public:
        entry["env"] = public
    if spec.allow:
        entry["allow"] = list(spec.allow)
    return entry, secret


# --------------------------------------------------------------------------- #
# writing: one block, never the whole file
# --------------------------------------------------------------------------- #
def _quote(text: str) -> str:
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _entry_block(entry: Dict[str, Any]) -> str:
    lines = ["[[mcp.servers]]", f"name = {_quote(entry['name'])}"]
    lines.append(f"command = {_quote(entry['command'])}")
    if entry.get("args"):
        lines.append("args = [" + ", ".join(_quote(str(a)) for a in entry["args"]) + "]")
    if entry.get("env"):
        pairs = ", ".join(f"{key} = {_quote(str(value))}" for key, value in entry["env"].items())
        lines.append(f"env = {{ {pairs} }}")
    if entry.get("timeout"):
        lines.append(f"timeout = {int(entry['timeout'])}")
    if entry.get("allow"):
        lines.append("allow = [" + ", ".join(_quote(str(t)) for t in entry["allow"]) + "]")
    return "\n".join(lines) + "\n"


def _split_blocks(text: str) -> tuple[str, List[str], str]:
    """Split ``config.toml`` into (head, mcp blocks, tail-after-mcp).

    Only the ``[[mcp.servers]]`` blocks are touched: comments elsewhere, the
    ``[providers]`` tables and the flat keys stay exactly as they were.
    """
    lines = text.splitlines(keepends=True)
    start: Optional[int] = None
    end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[[mcp.servers]]") and start is None:
            start = index
        elif start is not None and stripped.startswith("[") and not stripped.startswith("[[mcp.servers]]"):
            end = index
            break
    if start is None:
        return text, [], ""
    head = "".join(lines[:start])
    body = "".join(lines[start:end])
    tail = "".join(lines[end:])
    blocks = [chunk for chunk in re.split(r"(?=\[\[mcp\.servers\]\])", body) if chunk.strip()]
    return head, blocks, tail


def add_server(entry: Dict[str, Any], *, config_path: Optional[Path] = None) -> Path:
    """Append - or replace, by name - one ``[[mcp.servers]]`` block."""
    path = config_path or (uh_home() / "config.toml")
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    head, blocks, tail = _split_blocks(text)
    wanted = f'name = {_quote(entry["name"])}'
    kept = [block for block in blocks if wanted not in block]
    kept.append(_entry_block(entry))
    body = "\n".join(block.rstrip() + "\n" for block in kept)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{head}{body}{tail if tail else ''}", encoding="utf-8")
    return path


def secrets_path() -> Path:
    return uh_home() / SECRETS_FILE


def read_secrets() -> Dict[str, Dict[str, str]]:
    path = secrets_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    servers = data.get("servers")
    if not isinstance(servers, dict):
        return {}
    return {
        str(name): {str(key): str(value) for key, value in table.items()}
        for name, table in servers.items()
        if isinstance(table, dict)
    }


def write_secrets(name: str, values: Dict[str, str]) -> Path:
    """Store a server's secrets in their own file (0600), replacing its table."""
    path = secrets_path()
    existing = read_secrets()
    if values:
        existing[name] = dict(values)
    else:
        existing.pop(name, None)
    lines = ["# UH MCP secrets - never printed, never in config.toml.", ""]
    for server, table in sorted(existing.items()):
        lines.append(f"[servers.{server}]")
        for key, value in sorted(table.items()):
            lines.append(f"{key} = {_quote(value)}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def connect(
    ident: str, name: str, fields: Dict[str, str], *, config_path: Optional[Path] = None
) -> Dict[str, Any]:
    """Create a server from a template: entry in config, secrets beside it."""
    entry, secret = build(ident, name, fields)
    add_server(entry, config_path=config_path)
    if secret:
        write_secrets(entry["name"], secret)
    spec = template(ident)
    return {
        "name": entry["name"],
        "template": spec.ident,
        "label": spec.label,
        "can_upload": bool(spec.upload_tool),
        "config": str(config_path or (uh_home() / "config.toml")),
        "secrets": sorted(secret),
    }


def upload_plan(server: str, tool: str, path: Path, *, bucket: str = "", key: str = "") -> Dict[str, Any]:
    """Store one file with the cloud server's own upload tool.

    The bucket comes from the server's own ``S3_BUCKET`` when the operator set one
    at connect time; otherwise the call is refused with a sentence that says what
    to change. Returns a payload; a refusal is data, never an exception.
    """
    from . import mcp as uh_mcp

    servers, _problems = uh_mcp.servers_from_config()
    config = next((item for item in servers if item.name == server), None)
    if config is None:
        return {"requested": True, "ok": False, "error": f"unknown server '{server}'"}
    chosen = bucket or str(config.env.get("S3_BUCKET") or "").strip()
    if not chosen:
        return {
            "requested": True,
            "ok": False,
            "error": "no bucket: set S3_BUCKET when connecting, or pass one",
        }
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"requested": True, "ok": False, "error": f"cannot read {path}: {exc}"}
    object_key = key or f"uh/{path.name}"
    try:
        client = uh_mcp.client_for(config)
        toolset = uh_mcp.Toolset(client)
        result = toolset.call(
            f"mcp__{server}__{tool}",
            {
                "bucket": chosen,
                "key": object_key,
                "content": content,
                "contentType": "text/markdown; charset=utf-8",
            },
        )
    except Exception as exc:  # noqa: BLE001 - the plan is already on disk
        return {"requested": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict) and result.get("error"):
        return {
            "requested": True,
            "ok": False,
            "bucket": chosen,
            "key": object_key,
            "error": str(result.get("error")),
        }
    return {"requested": True, "ok": True, "bucket": chosen, "key": object_key}


def upload_targets() -> List[Dict[str, str]]:
    """Configured servers that can store a file, with the tool they use.

    A server counts only when its package matches a template that names an upload
    tool - "it has secrets" is not the same thing as "it can write".
    """
    try:
        from . import mcp as uh_mcp

        servers, _problems = uh_mcp.servers_from_config()
    except Exception:  # noqa: BLE001 - the panel must render on a broken config
        return []
    out: List[Dict[str, str]] = []
    for server in servers:
        joined = " ".join([server.command, *server.args])
        for spec in TEMPLATES.values():
            if not spec.upload_tool:
                continue
            if any(str(arg) in joined for arg in spec.args[1:] or spec.args):
                out.append({"name": server.name, "tool": spec.upload_tool, "label": spec.label})
                break
    return out


__all__ = [
    "CloudError",
    "Field",
    "SECRETS_FILE",
    "TEMPLATES",
    "Template",
    "add_server",
    "build",
    "check_name",
    "connect",
    "read_secrets",
    "secrets_path",
    "template",
    "template_list",
    "upload_targets",
    "write_secrets",
]
