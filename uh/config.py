"""Layered configuration for UH (Jalon 3).

Precedence, lowest to highest::

    defaults  <  ~/.uh/config.toml  <  ./uh.toml  <  UH_* environment  <  CLI flags

Reading uses :mod:`tomllib` (stdlib, Python >= 3.11). Writing uses the small
serialiser below, which handles **flat tables only**: booleans, integers and
strings. Arrays, nested tables and datetimes are deliberately unsupported -
they raise, they are never silently mangled. A hand-written ``config.toml``
mentioning an unknown key produces a warning and the key is ignored; it is not
an error, so a newer file never breaks an older build.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

DEFAULTS: dict[str, Any] = {
    "default_provider": "ollama",
    "default_model": "",
    "omnirouter_model": "auto/best-free",
    "max_iterations": 8,
    "workspace": "",
    "store_path": "$UH_HOME/uh.db",
    "stream": True,
    "banner": True,
}

KEY_TYPES: dict[str, type] = {
    "default_provider": str,
    "default_model": str,
    "omnirouter_model": str,
    "max_iterations": int,
    "workspace": str,
    "store_path": str,
    "stream": bool,
    "banner": bool,
}

ENV_KEYS: dict[str, str] = {
    "default_provider": "UH_DEFAULT_PROVIDER",
    "default_model": "UH_DEFAULT_MODEL",
    "omnirouter_model": "UH_OMNIROUTER_MODEL",
    "max_iterations": "UH_MAX_ITERATIONS",
    "workspace": "UH_WORKSPACE",
    "store_path": "UH_STORE_PATH",
    "stream": "UH_STREAM",
    "banner": "UH_BANNER",
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

HEADER = (
    "# UH configuration - written by `uh config set`.\n"
    "# Flat keys only: no tables, no arrays, no datetimes.\n"
    "\n"
)


class ConfigError(RuntimeError):
    """Invalid key, invalid value or unreadable configuration file."""


def uh_home() -> Path:
    raw = os.environ.get("UH_HOME")
    return (Path(raw).expanduser() if raw else Path.home() / ".uh").resolve()


def user_config_path() -> Path:
    return uh_home() / "config.toml"


def project_config_path(cwd: Optional[Path] = None) -> Path:
    return ((cwd or Path.cwd()) / "uh.toml").resolve()


def coerce(key: str, raw: Any) -> Any:
    """Convert ``raw`` to the declared type of ``key`` or raise :class:`ConfigError`."""
    if key not in KEY_TYPES:
        raise ConfigError(
            f"unknown config key '{key}'; known keys: {', '.join(sorted(KEY_TYPES))}"
        )
    kind = KEY_TYPES[key]
    if kind is bool:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ConfigError(f"'{key}' expects a boolean (true/false), got '{raw}'")
    if kind is int:
        if isinstance(raw, bool):  # bool is an int subclass: reject it explicitly
            raise ConfigError(f"'{key}' expects an integer, got a boolean")
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"'{key}' expects an integer, got '{raw}'") from exc
    return str(raw)


def read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a table")
    return data


def _quote(text: str) -> str:
    out: list[str] = []
    for character in text:
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


def dump_flat_toml(values: dict[str, Any]) -> str:
    """Serialise flat key/value pairs. Non-scalar values raise :class:`ConfigError`."""
    lines = [HEADER.rstrip("\n"), ""]
    for key in sorted(values):
        value = values[key]
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = _quote(value)
        else:
            raise ConfigError(
                f"cannot write '{key}': only booleans, integers and strings are supported"
            )
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def write_layer(layer: str, key: str, raw_value: Any) -> Path:
    """Set one key in the ``user`` or ``project`` layer, preserving the others."""
    if layer not in {"user", "project"}:
        raise ConfigError(f"unknown layer '{layer}'; use 'user' or 'project'")
    value = coerce(key, raw_value)
    path = user_config_path() if layer == "user" else project_config_path()

    existing = read_toml(path)
    for other_key in list(existing):
        if other_key not in KEY_TYPES:
            del existing[other_key]  # unknown key from a newer build: dropped, warned at load
    existing[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_flat_toml(existing), encoding="utf-8")
    return path


@dataclass
class Config:
    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def get(self, key: str) -> Any:
        return self.values.get(key, DEFAULTS.get(key))

    def source(self, key: str) -> str:
        return self.sources.get(key, "default")

    def resolved_store_path(self) -> Path:
        raw = str(self.get("store_path") or "")
        raw = raw.replace("$UH_HOME", str(uh_home()))
        if not raw:
            return uh_home() / "uh.db"
        return Path(raw).expanduser()


def load(cli_overrides: Optional[dict[str, Any]] = None, cwd: Optional[Path] = None) -> Config:
    """Read every layer; later layers win. Unknown keys warn and are ignored."""
    config = Config(values=dict(DEFAULTS), sources={key: "default" for key in DEFAULTS})

    layers = (("user", user_config_path()), ("project", project_config_path(cwd)))
    for layer_name, path in layers:
        try:
            data = read_toml(path)
        except ConfigError as exc:
            config.warnings.append(str(exc))
            continue
        for key, value in data.items():
            if key not in KEY_TYPES:
                config.warnings.append(f"{path}: unknown config key '{key}' ignored")
                continue
            if not isinstance(value, KEY_TYPES[key]) or isinstance(value, bool) and KEY_TYPES[key] is int:
                config.warnings.append(
                    f"{path}: '{key}' expects {KEY_TYPES[key].__name__}, got "
                    f"{type(value).__name__} - ignored"
                )
                continue
            config.values[key] = value
            config.sources[key] = layer_name

    for key, env_name in ENV_KEYS.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            config.values[key] = coerce(key, raw)
        except ConfigError as exc:
            config.warnings.append(f"{env_name}: {exc} - ignored")
            continue
        config.sources[key] = "env"

    for key, value in (cli_overrides or {}).items():
        if value is None:
            continue
        if key not in KEY_TYPES:
            config.warnings.append(f"unknown CLI override '{key}' ignored")
            continue
        try:
            config.values[key] = coerce(key, value)
        except ConfigError as exc:
            config.warnings.append(f"--{key}: {exc} - ignored")
            continue
        config.sources[key] = "cli"

    return config
