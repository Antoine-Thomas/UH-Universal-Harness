"""Skills: markdown files the operator owns, in a folder they can open.

A skill is one ``.md`` file - a title, a one-line description, and instructions
written in plain prose. No database, no index, no export step: if you can read it
in VS Code or Obsidian you own it, and the archive in :mod:`uh.backup` carries it
for free.

Two roots, scanned in this order:

* ``$UH_HOME/skills`` - yours. Creating one writes a file there, never anywhere
  else;
* ``uh/skills`` inside the package - the ones shipped with UH. They are read-only
  from here: a native skill that the user can edit is a native skill that the next
  upgrade overwrites.

The parser is deliberately forgiving (a skill written by hand in a text editor
must not need a schema) and the writer is deliberately strict (a name that could
escape the folder is refused, not sanitised).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import uh_home

#: A filename the API will accept: lowercase, digits, dash, underscore.
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: Longest acceptable title/description, so a paste cannot fill the folder.
MAX_TITLE = 120
MAX_DESCRIPTION = 240
MAX_INSTRUCTIONS = 20_000
#: Shipped skills, next to this module.
NATIVE_ROOT = Path(__file__).resolve().parent / "skills"

TEMPLATE = """# {title}

> {description}

## Instructions

{instructions}
"""


class SkillError(ValueError):
    """The skill cannot be read or written; the message says why."""


def user_dir(home: Optional[Path] = None) -> Path:
    return (home or uh_home()) / "skills"


def native_dir() -> Path:
    return NATIVE_ROOT


def _slug(value: str) -> str:
    """A title into a filename: accents folded, spaces dashed, nothing exotic."""
    text = (value or "").strip().lower()
    for source, target in (
        ("àâäá", "a"),
        ("éèêë", "e"),
        ("îïí", "i"),
        ("ôöó", "o"),
        ("ùûüú", "u"),
        ("ç", "c"),
        ("ñ", "n"),
    ):
        for char in source:
            text = text.replace(char, target)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:64]


def parse(text: str, fallback_name: str = "") -> Dict[str, str]:
    """Title, description and instructions out of a markdown file.

    The conventions are loose on purpose: ``# Title``, an optional ``> line``
    description, an optional ``## Instructions`` heading. Anything else is
    instructions - a hand-written file is a valid skill.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = ""
    description = ""
    instructions: List[str] = []
    in_instructions = False
    started = False
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not in_instructions and not title and stripped.startswith("# "):
            title = stripped[2:].strip()
            continue
        if not in_instructions and stripped.startswith("## "):
            # Only an "instructions" heading starts the body; any other heading is
            # part of it, so a hand-written file keeps its structure.
            if "instruction" in stripped.lower():
                in_instructions = True
                started = True
            else:
                instructions.append(line)
                started = True
            continue
        if not in_instructions and not started and not description and stripped.startswith(">"):
            description = stripped[1:].strip()
            continue
        instructions.append(line)
        if stripped:
            started = True
    body = "\n".join(instructions).strip()
    return {
        "title": title or fallback_name or "Untitled skill",
        "description": description[:MAX_DESCRIPTION],
        "instructions": body[:MAX_INSTRUCTIONS],
    }


def _entry(path: Path, origin: str) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
        stat = path.stat()
    except OSError:
        return None
    parsed = parse(text, fallback_name=path.stem)
    return {
        "name": path.stem,
        "title": parsed["title"],
        "description": parsed["description"],
        "instructions_chars": len(parsed["instructions"]),
        "origin": origin,
        "native": origin == "native",
        "path": str(path),
        "bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(
            timespec="seconds"
        ),
    }


def list_skills(home: Optional[Path] = None) -> Dict[str, Any]:
    """Both roots, user first. A missing folder is an empty list, not an error."""
    found: List[Dict[str, Any]] = []
    for origin, folder in (("user", user_dir(home)), ("native", native_dir())):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            entry = _entry(path, origin)
            if entry is not None:
                found.append(entry)
    return {
        "skills": found,
        "user": sum(1 for entry in found if entry["origin"] == "user"),
        "native": sum(1 for entry in found if entry["origin"] == "native"),
        "user_dir": str(user_dir(home)),
        "native_dir": str(native_dir()),
        "native_available": native_dir().is_dir() and any(native_dir().glob("*.md")),
    }


def read_skill(name: str, home: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """One skill's full text, user root first, then native."""
    stem = (name or "").strip()
    if not NAME.match(stem):
        return None
    for origin, folder in (("user", user_dir(home)), ("native", native_dir())):
        path = folder / f"{stem}.md"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        parsed = parse(text, fallback_name=stem)
        return {
            "name": stem,
            "origin": origin,
            "native": origin == "native",
            "path": str(path),
            "text": text,
            **parsed,
        }
    return None


def create(
    *,
    title: str,
    description: str,
    instructions: str,
    name: Optional[str] = None,
    home: Optional[Path] = None,
) -> Dict[str, Any]:
    """Write one new skill file. Refuses anything that stays outside the folder."""
    clean_title = (title or "").strip()[:MAX_TITLE]
    clean_description = (description or "").strip()[:MAX_DESCRIPTION]
    clean_instructions = (instructions or "").strip()[:MAX_INSTRUCTIONS]
    if not clean_title:
        raise SkillError("a skill needs a title")
    if not clean_instructions:
        raise SkillError("a skill needs instructions: a title alone teaches nothing")
    stem = (name or _slug(clean_title)).strip().lower()
    if not NAME.match(stem):
        raise SkillError(
            "the name becomes a file name: lowercase letters, digits, - and _ only"
        )
    folder = user_dir(home)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.md"
    if path.exists():
        raise SkillError(f"{stem}.md already exists: pick another name")
    text = TEMPLATE.format(
        title=clean_title, description=clean_description, instructions=clean_instructions
    )
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise SkillError(f"cannot write {path}: {exc}") from exc
    return {
        "name": stem,
        "path": str(path),
        "bytes": path.stat().st_size,
        "title": clean_title,
        "description": clean_description,
    }


__all__ = [
    "MAX_DESCRIPTION",
    "MAX_INSTRUCTIONS",
    "MAX_TITLE",
    "NAME",
    "NATIVE_ROOT",
    "SkillError",
    "TEMPLATE",
    "create",
    "list_skills",
    "native_dir",
    "parse",
    "read_skill",
    "user_dir",
]
