"""Which skills the operator has turned off — the only persisted skill preference.

A skill is one flat ``.md`` file (see :mod:`uh.skills`). This module stores the
**exceptions**: the disabled ones, by stable id ``"<origin>:<name>"``. Everything
else is enabled, so installing a skill never needs a preference entry, and a skill
that disappears is simply absent from the set — it is never "silently re-enabled",
because nothing here decides anything but "was it explicitly turned off?".

Rules, and why each one exists:

* **atomic write** (tmp file + ``os.replace``): a crash mid-write must not truncate
  the preferences and lose every choice;
* **ids only, never paths**: a client cannot hand a path, so a preference cannot
  become a directory traversal;
* **a corrupt file is empty, not fatal**: UH must boot with a broken JSON;
* **native files are never touched**: turning off a native skill is a preference,
  not an edit of the file shipped with UH.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

FILE_NAME = "skill_prefs.json"
#: Bounded: this file is a list of names, not a place to paste data.
MAX_ENTRIES = 2000


class SkillPrefsError(ValueError):
    """The preferences cannot be written; the message says why."""


def prefs_path(home: Optional[Path] = None) -> Path:
    from .config import uh_home

    return (home or uh_home()) / FILE_NAME


def skill_id(name: str, origin: str = "user") -> str:
    """Stable id: origin and name, never a path."""
    kind = "native" if str(origin) in ("native", "inherited") else "user"
    return f"{kind}:{str(name).strip()}"


def _read(home: Optional[Path]) -> Dict[str, Any]:
    path = prefs_path(home)
    if not path.is_file():
        return {"disabled": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupted file must not stop UH: every skill comes back enabled, and the
        # broken file stays on disk for the operator to inspect.
        return {"disabled": []}
    if not isinstance(raw, dict):
        return {"disabled": []}
    disabled = raw.get("disabled")
    if not isinstance(disabled, list):
        disabled = []
    return {"disabled": [str(item) for item in disabled if isinstance(item, str)][:MAX_ENTRIES]}


def _write(home: Optional[Path], data: Dict[str, Any]) -> None:
    path = prefs_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"disabled": sorted(set(data["disabled"]))}, ensure_ascii=False, indent=0)
    handle, temp = tempfile.mkstemp(prefix=".skill_prefs-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except OSError as exc:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise SkillPrefsError(f"cannot write {path}: {exc}") from exc


def disabled(home: Optional[Path] = None) -> List[str]:
    return list(_read(home)["disabled"])


def is_enabled(name: str, origin: str = "user", home: Optional[Path] = None) -> bool:
    return skill_id(name, origin) not in set(_read(home)["disabled"])


def set_enabled(
    name: str, origin: str, enabled: bool, home: Optional[Path] = None
) -> Dict[str, Any]:
    """Turn one skill on or off. Idempotent, atomic, and bounded."""
    identifier = skill_id(name, origin)
    if not identifier.split(":", 1)[1]:
        raise SkillPrefsError("a skill id needs a name")
    data = _read(home)
    current = set(data["disabled"])
    if enabled:
        current.discard(identifier)
    else:
        if len(current) >= MAX_ENTRIES:
            raise SkillPrefsError("too many disabled skills")
        current.add(identifier)
    _write(home, {"disabled": sorted(current)})
    return {"id": identifier, "enabled": enabled, "disabled": sorted(current)}


def for_listing(listing: Iterable[Mapping[str, Any]], home: Optional[Path] = None) -> List[dict]:
    """Mark every entry with ``enabled``, and keep the disabled ones in the list.

    The interface must *show* a disabled skill (with its checkbox off) — filtering it
    out here would make it unreachable, and the operator could never turn it back on.
    """
    off = set(_read(home)["disabled"])
    marked: List[dict] = []
    for entry in listing or ():
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "")
        origin = str(entry.get("origin") or "user")
        item = dict(entry)
        item["id"] = skill_id(name, origin)
        item["enabled"] = item["id"] not in off
        item["origin"] = "native" if origin in ("native", "inherited") else "user"
        marked.append(item)
    return marked


def enabled_only(
    listing: Iterable[Mapping[str, Any]], home: Optional[Path] = None
) -> List[dict]:
    """The skills the router may see: enabled, named, and nothing else."""
    return [entry for entry in for_listing(listing, home) if entry.get("enabled")]


__all__ = [
    "FILE_NAME",
    "SkillPrefsError",
    "disabled",
    "enabled_only",
    "for_listing",
    "is_enabled",
    "prefs_path",
    "set_enabled",
    "skill_id",
]
