"""User-supplied API keys for OpenAI-compatible providers (Jalon 4).

Storage: ``$UH_HOME/keys.toml`` (``~/.uh/keys.toml`` by default), mode ``0600``
where the platform honours it. Layout::

    # UH provider keys - never commit this file.
    [providers.mygateway]
    base_url = "https://api.example.com/v1"
    model = "some-model"
    key = "sk-..."

Rules enforced here:

* no key is ever written into the repository, the package, the tests or the
  examples - this module only reads and writes the user's own home;
* no function returns a key for display: callers get ``present``/``absent``;
* the file is rewritten atomically (temp file + replace) so a crash cannot
  leave a half-written secret.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from ._compat import tomllib

FILE_NAME = "keys.toml"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")

HEADER = (
    "# UH provider keys - never commit this file.\n"
    "# Written by `uh providers add`; mode 0600 where the OS supports it.\n"
)


def uh_home() -> Path:
    raw = os.environ.get("UH_HOME")
    return (Path(raw).expanduser() if raw else Path.home() / ".uh").resolve()


def keys_path() -> Path:
    return uh_home() / FILE_NAME


def _quote(text: str) -> str:
    out: list[str] = []
    for character in str(text):
        if character == "\\":
            out.append("\\\\")
        elif character == '"':
            out.append('\\"')
        elif character == "\n":
            out.append("\\n")
        elif character == "\t":
            out.append("\\t")
        elif character == "\r":
            out.append("\\r")
        elif ord(character) < 0x20 or ord(character) == 0x7F:
            out.append(f"\\u{ord(character):04x}")
        else:
            out.append(character)
    return '"' + "".join(out) + '"'


def read_providers() -> dict[str, dict[str, Any]]:
    path = keys_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for name, entry in providers.items():
        if isinstance(entry, dict) and entry.get("base_url"):
            single = str(entry.get("key") or "")
            many = [str(item) for item in (entry.get("keys") or []) if item]
            if single and single not in many:
                many.insert(0, single)
            cleaned[str(name)] = {
                "base_url": str(entry.get("base_url") or ""),
                "model": str(entry.get("model") or ""),
                "key": many[0] if many else "",
                "keys": many,
            }
    return cleaned


def provider_names() -> list[str]:
    return sorted(read_providers())


def get_provider(name: str) -> Optional[dict[str, Any]]:
    return read_providers().get((name or "").strip().lower())


def has_key(name: str) -> bool:
    entry = get_provider(name)
    return bool(entry and entry.get("key"))


def _dump(providers: dict[str, dict[str, Any]]) -> str:
    lines = [HEADER.rstrip("\n"), ""]
    for name in sorted(providers):
        entry = providers[name]
        lines.append(f"[providers.{name}]")
        lines.append(f"base_url = {_quote(entry.get('base_url', ''))}")
        lines.append(f"model = {_quote(entry.get('model', ''))}")
        keys = [key for key in (entry.get("keys") or []) if key]
        if len(keys) > 1:
            rendered = ", ".join(_quote(key) for key in keys)
            lines.append(f"keys = [{rendered}]")
        else:
            lines.append(f"key = {_quote(entry.get('key', '') or (keys[0] if keys else ''))}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _write_all(providers: dict[str, dict[str, Any]]) -> Path:
    path = keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _dump(providers)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=str(path.parent), prefix=".keys-", suffix=".tmp"
    )
    try:
        with handle:
            handle.write(payload)
        os.replace(handle.name, path)
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    _restrict(path)
    return path


def _restrict(path: Path) -> None:
    """Best effort 0600: POSIX honours it, Windows may only clear the read-only bit."""
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - filesystems without chmod
        pass


def add_key(name: str, key: str) -> Path:
    """Append a key to an existing provider (rotation), ignoring duplicates."""
    clean = (name or "").strip().lower()
    providers = read_providers()
    if clean not in providers:
        raise ValueError(f"unknown provider '{clean}': add it with `uh providers add` first")
    entry = providers[clean]
    keys = [item for item in (entry.get("keys") or []) if item]
    if not keys and entry.get("key"):
        keys = [entry["key"]]
    if key and key not in keys:
        keys.append(key)
    entry["keys"] = keys
    entry["key"] = keys[0] if keys else ""
    return _write_all(providers)


def remove_key(name: str, key: str) -> bool:
    """Drop one key (by exact value); never prints it back."""
    clean = (name or "").strip().lower()
    providers = read_providers()
    entry = providers.get(clean)
    if not entry:
        return False
    keys = [item for item in (entry.get("keys") or []) if item and item != key]
    if len(keys) == len([item for item in (entry.get("keys") or []) if item]):
        return False
    entry["keys"] = keys
    entry["key"] = keys[0] if keys else ""
    _write_all(providers)
    return True


def add_provider(name: str, base_url: str, model: str, key: str) -> Path:
    clean = (name or "").strip().lower()
    if not _NAME_RE.match(clean):
        raise ValueError(
            "provider name must be lowercase letters, digits, '-' or '_' (max 32 chars)"
        )
    if not (base_url or "").startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    providers = read_providers()
    providers[clean] = {
        "base_url": (base_url or "").rstrip("/"),
        "model": model or "",
        "key": key or "",
        "keys": [key] if key else [],
    }
    return _write_all(providers)


def remove_provider(name: str) -> bool:
    providers = read_providers()
    clean = (name or "").strip().lower()
    if clean not in providers:
        return False
    del providers[clean]
    _write_all(providers)
    return True


def file_mode(path: Optional[Path] = None) -> str:
    """Human description of permissions, for ``uh doctor`` (never the content)."""
    target = path or keys_path()
    if not target.is_file():
        return "absent"
    if os.name == "nt":
        return "present (best-effort 0600 on Windows)"
    try:
        return f"present (mode {oct(target.stat().st_mode & 0o777)})"
    except OSError:  # pragma: no cover
        return "present"
