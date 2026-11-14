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

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ._compat import tomllib  # 3.10 gets tomli through the shim
from .config import uh_home

SECRETS_FILE = "mcp-secrets.toml"
#: Windows commands must not flash a console window for every write.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
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
    "self-repair": Template(
        ident="self-repair",
        label="Self-repair (UH source, lecture seule)",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "{{REPO_PATH}}"),
        what="Lire le code de UH pour proposer un correctif. Aucun outil d'écriture.",
        fields=(
            Field(
                "REPO_PATH",
                "Dépôt UH",
                placeholder="C:/Users/moi/UH-Universal-Harness",
                help="Le serveur ne voit que ce dossier : ~/.uh (clés, secrets) reste hors de portée.",
            ),
        ),
        allow=(
            "read_text_file",
            "read_file",
            "read_multiple_files",
            "list_directory",
            "directory_tree",
            "search_files",
            "get_file_info",
            "list_allowed_directories",
        ),
        upload_note="Lecture seule par construction : la modification passe par `uh fix`, qui montre un diff avant d'écrire.",
        docs="https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem",
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
    args = [str(arg) for arg in spec.args]
    # A field can be part of the command line (`{{REPO_PATH}}`): the filesystem
    # server takes the folder it may see as an argument, not as an env var.
    for item in spec.fields:
        value = values.get(item.key, "")
        if value:
            args = [arg.replace("{{" + item.key + "}}", value) for arg in args]
    unresolved = [arg for arg in args if "{{" in arg]
    if unresolved:
        raise CloudError("unfilled placeholder in args: " + ", ".join(unresolved))
    entry: Dict[str, Any] = {
        "name": entry_name,
        "command": spec.command,
        "args": args,
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
    if entry.get("transport"):
        # An HTTP/SSE server needs no command: it is a URL. The stdio client skips
        # it (see `servers_from_config`), and the panel says so instead of pretending.
        lines.append(f"transport = {_quote(entry['transport'])}")
        lines.append(f"url = {_quote(entry.get('url', ''))}")
        if entry.get("timeout"):
            lines.append(f"timeout = {int(entry['timeout'])}")
        return "\n".join(lines) + "\n"
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


def _decode_console(raw: bytes) -> str:
    """Decode a Windows console program's output whatever its code page is."""
    for encoding in ("utf-8", "oem", "cp1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def restrict(path: Path) -> str:
    """Make a secrets file readable by its owner only. Returns what was done.

    ``chmod`` is a no-op on Windows: the ACL is what decides, so the file is
    restricted with ``icacls`` (inheritance removed, one ACE for the current
    user). Both paths are best effort: a failure must not lose the token that
    was just written, but the caller can read the returned label and the panel
    can say "permissions not verified" instead of pretending.
    """
    if not path.is_file():
        return "absent"
    if os.name == "nt":
        user = os.environ.get("USERNAME") or ""
        if not user:
            return "acl-not-applied"
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                capture_output=True,
                timeout=10,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return "acl-not-applied"
        return "acl-owner-only" if done.returncode == 0 else "acl-not-applied"
    try:
        path.chmod(0o600)
    except OSError:
        return "chmod-not-applied"
    return "0600"


def permissions(path: Optional[Path] = None) -> Dict[str, Any]:
    """What is known about the secrets file's protection, for tests and the panel.

    ``owner_only`` is True only when it was verified, never assumed: a POSIX mode
    with no group/other bits, or a Windows ACL where nobody except the current
    user and SYSTEM/Administrators has an entry.
    """
    target = path or secrets_path()
    if not target.is_file():
        return {"path": str(target), "exists": False, "owner_only": True, "detail": "absent"}
    if os.name == "nt":
        try:
            listed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["icacls", str(target)],
                capture_output=True,
                timeout=10,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return {
                "path": str(target),
                "exists": True,
                "owner_only": False,
                "detail": "icacls indisponible : permissions non vérifiées",
            }
        # icacls writes in the console's OEM code page, not UTF-8: decoding it as
        # UTF-8 throws inside subprocess's reader thread and the answer becomes
        # "no ACE found", which is the opposite of the truth.
        text = _decode_console(listed.stdout or b"")
        user = (os.environ.get("USERNAME") or "").upper()
        # icacls localises its principal names (this machine says "AUTORITE
        # NT\Système" and "DROITS DU PROPRIÉTAIRE"), so the decision is taken on
        # the trailing name, never on an English-only sentence.
        trusted = {
            "SYSTEM",
            "SYSTÈME",
            "ADMINISTRATORS",
            "ADMINISTRATEURS",
            "OWNER RIGHTS",
            "DROITS DU PROPRIÉTAIRE",
        }
        widened: List[str] = []
        body = text.replace(str(target), "").replace(target.name, "")
        for line in body.splitlines():
            if ":(" not in line:
                continue
            principal = line.split(":(")[0].strip().upper()
            if not principal:
                continue
            trailer = principal.rsplit("\\", 1)[-1].strip()
            if trailer in trusted or (user and trailer == user):
                continue
            widened.append(line.strip())
        return {
            "path": str(target),
            "exists": True,
            "owner_only": not widened,
            "detail": "acl" if not widened else "acl trop large : " + "; ".join(widened)[:300],
        }
    mode = target.stat().st_mode & 0o777
    return {
        "path": str(target),
        "exists": True,
        "owner_only": (mode & 0o077) == 0,
        "detail": f"{mode:04o}",
    }


def masked(value: str) -> str:
    """``••••`` plus the last four characters. The UI never receives more."""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "••••"
    return "••••" + text[-4:]


def read_secrets() -> Dict[str, Dict[str, str]]:
    path = secrets_path()
    if not path.is_file():
        return {}
    if os.name != "nt":
        try:
            # Self-heal a file a copy, an unzip or an old version widened: the
            # token is already inside, reading it back needs no permission to
            # leave it readable.
            if (path.stat().st_mode & 0o077) != 0:
                path.chmod(0o600)
        except OSError:
            pass
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
    """Store a server's secrets in their own file (owner-only), replacing its table."""
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
    restrict(path)
    return path


def redact(text: str) -> str:
    """Remove every stored secret value from a string before it is shown.

    Server stderr is quoted in error messages to say *why* a launch failed, and
    a server that dumps its environment would otherwise put the token in the
    browser, the log and the report in one go.
    """
    value = str(text or "")
    for table in read_secrets().values():
        for secret in table.values():
            if len(secret) >= 4 and secret in value:
                value = value.replace(secret, masked(secret))
    return value


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



SECRET_HINTS = ("TOKEN", "SECRET", "KEY", "PASSWORD", "CREDENTIAL", "PASS")


def _looks_secret(name: str) -> bool:
    """A name that says "credential" is stored like one; the rest stays readable."""
    return any(hint in name.upper() for hint in SECRET_HINTS)


def connect_custom(
    name: str,
    command: str,
    args: Sequence[str] = (),
    env: Optional[Dict[str, str]] = None,
    *,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Any MCP server the user can spell: no template, same rules.

    ``env`` entries whose *name* looks like a credential go to
    ``mcp-secrets.toml``; the rest stay in ``config.toml`` where a reader can see
    what a server was configured with. Nothing here starts the server: the caller
    re-reads the config and lets the panel say `connected` once it parses.
    """
    entry_name = check_name(name)
    cleaned = (command or "").strip()
    if not cleaned:
        raise CloudError("command is required (e.g. npx, uvx, C:/tools/serve.exe)")
    values = {
        str(key).strip(): str(value).strip()
        for key, value in (env or {}).items()
        if str(key).strip() and str(value).strip()
    }
    public = {key: value for key, value in values.items() if not _looks_secret(key)}
    secret = {key: value for key, value in values.items() if _looks_secret(key)}
    entry: Dict[str, Any] = {
        "name": entry_name,
        "command": cleaned,
        "args": [str(arg).strip() for arg in args if str(arg).strip()],
        "timeout": 60,
    }
    if public:
        entry["env"] = public
    add_server(entry, config_path=config_path)
    if secret:
        write_secrets(entry_name, secret)
    return {
        "name": entry_name,
        "custom": True,
        "config": str(config_path or (uh_home() / "config.toml")),
        "secrets": sorted(secret),
    }



HTTP_NOTE = (
    "Serveur HTTP/SSE enregistré : UH lui parle directement, et demande ton accord "
    "avant chaque appel d'outil."
)


def connect_http(
    name: str,
    url: str,
    *,
    label: str = "",
    token: str = "",
    allow_insecure: bool = False,
    config_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record an HTTP/SSE MCP server (Positron, an IDE, a hosted tool server).

    The URL goes through :mod:`uh.urlguard` *before* anything is written: only
    http(s), no credentials in the address, and plain HTTP to a remote host is
    refused unless the caller explicitly allowed it (the token would travel in
    clear). A token, if given, goes to the owner-only secrets file like every
    other secret and is never returned - only its masked form.
    """
    from . import urlguard

    entry_name = check_name(name)
    try:
        checked = urlguard.check(url, allow_insecure=allow_insecure)
    except urlguard.URLRejected as exc:
        raise CloudError(str(exc)) from exc

    entry: Dict[str, Any] = {
        "name": entry_name,
        "transport": "http",
        "url": checked.url,
        "timeout": 60,
    }
    if checked.insecure:
        # Persisted so a restart keeps warning instead of quietly forgetting.
        entry["insecure"] = True
    if label:
        entry["note"] = label
    add_server(entry, config_path=config_path)
    if token:
        write_secrets(entry_name, {"MCP_HTTP_TOKEN": token})
    return {
        "name": entry_name,
        "transport": "http",
        "url": checked.url,
        "state": "configured-not-started",
        "reason": HTTP_NOTE,
        "warning": checked.warning,
        "insecure": checked.insecure,
        "token": masked(token) if token else "",
        "secrets": ["MCP_HTTP_TOKEN"] if token else [],
    }


def pending_http_servers(*, config_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The HTTP/SSE entries `config.toml` holds, so the panel can show them.

    The token is reported masked (``••••`` plus its last four characters) and
    read from the secrets file at call time: ``config.toml`` never contains it,
    and nothing here hands the browser a value it could replay.
    """
    path = config_path or (uh_home() / "config.toml")
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    secrets = read_secrets()
    section = data.get("mcp") or {}
    rows = []
    for entry in section.get("servers") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("transport") or "") != "http":
            continue
        name = str(entry.get("name") or "")
        token = str((secrets.get(name) or {}).get("MCP_HTTP_TOKEN") or "")
        rows.append(
            {
                "name": name,
                "url": str(entry.get("url") or ""),
                "transport": "http",
                "insecure": bool(entry.get("insecure")),
                "token": masked(token),
                "has_token": bool(token),
                "allow": [str(item) for item in (entry.get("allow") or [])],
                "state": "configured-not-started",
                "reason": HTTP_NOTE,
            }
        )
    return rows


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
    "connect_custom",
    "HTTP_NOTE",
    "connect_http",
    "masked",
    "pending_http_servers",
    "permissions",
    "read_secrets",
    "redact",
    "restrict",
    "secrets_path",
    "template",
    "template_list",
    "upload_targets",
    "write_secrets",
]
