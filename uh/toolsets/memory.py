"""Memory toolset: a flat markdown file, no database (Jalon 1).

Layout of ``~/.uh/MEMORY.md`` (``UH_HOME`` overrides ``~/.uh``)::

    # UH memory

    ## Notes
    - free text

    ## Preferences
    - free text

    ## Learned
    - free text

Three fixed sections, in that order. ``memory_write`` replaces the body of one
section and leaves every other section byte-identical; unknown sections found
in an existing file are preserved at the end. The file is created on the first
write. A persistent, indexed store replaces this in Jalon 3 - nothing here is
meant to scale or to be searched.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List

SECTIONS: tuple[str, ...] = ("Notes", "Preferences", "Learned")
HEADER = "# UH memory"
MAX_CONTENT_CHARS = 20_000

TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": "Read the whole memory file and its sections.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": (
                "Replace one memory section (Notes, Preferences or Learned). "
                "Other sections are left untouched."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": list(SECTIONS)},
                    "content": {"type": "string"},
                },
                "required": ["section", "content"],
            },
        },
    },
]

ALLOWED_ARGS = {"memory_read": set(), "memory_write": {"section", "content"}}
REQUIRED_ARGS = {"memory_read": set(), "memory_write": {"section", "content"}}

_SECTION_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$")


def uh_home() -> Path:
    """``UH_HOME`` if set, otherwise ``~/.uh``."""
    raw = os.environ.get("UH_HOME")
    return (Path(raw).expanduser() if raw else Path.home() / ".uh").resolve()


def memory_path() -> Path:
    return uh_home() / "MEMORY.md"


def render(sections: Dict[str, str], extra_order: List[str] | None = None) -> str:
    """Render the canonical file: header, then the three sections, then extras."""
    order = list(SECTIONS) + [name for name in (extra_order or []) if name not in SECTIONS]
    blocks = [HEADER, ""]
    for name in order:
        body = (sections.get(name) or "").strip("\n")
        blocks.append(f"## {name}")
        if body:
            blocks.append(body)
        blocks.append("")
    return "\n".join(blocks).rstrip("\n") + "\n"


def parse(text: str) -> tuple[Dict[str, str], List[str]]:
    """Split a memory file into ``{section: body}`` plus the section order."""
    sections: Dict[str, str] = {name: "" for name in SECTIONS}
    order: List[str] = []
    current: str | None = None
    buffer: List[str] = []

    def flush() -> None:
        if current is not None:
            sections[current] = "\n".join(buffer).strip("\n")

    for line in text.splitlines():
        match = _SECTION_RE.match(line)
        if match:
            flush()
            current = match.group("name").strip()
            order.append(current)
            sections.setdefault(current, "")
            buffer = []
            continue
        if current is not None:
            buffer.append(line)
    flush()
    return sections, order


class MemoryToolset:
    name = "memory"
    tools = TOOLS

    def __init__(self, home: str | os.PathLike[str] | None = None) -> None:
        self.home = Path(home).expanduser().resolve() if home else uh_home()
        self.path = self.home / "MEMORY.md"

    def call(self, name: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        handler = {"memory_read": self.memory_read, "memory_write": self.memory_write}.get(name)
        if handler is None:
            return {"error": f"unknown memory tool '{name}'. Known: {sorted(ALLOWED_ARGS)}"}

        unexpected = set(args) - ALLOWED_ARGS[name]
        if unexpected:
            return {"error": f"unexpected argument(s) for {name}: {sorted(unexpected)}"}
        missing = REQUIRED_ARGS[name] - set(args)
        if missing:
            return {"error": f"missing argument(s) for {name}: {sorted(missing)}"}

        try:
            return handler(**args)
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- tools ------------------------------------------------------------- #
    def memory_read(self) -> dict:
        if not self.path.exists():
            return {
                "path": str(self.path),
                "exists": False,
                "content": "",
                "sections": {name: "" for name in SECTIONS},
            }
        content = self.path.read_text(encoding="utf-8", errors="replace")
        sections, order = parse(content)
        return {
            "path": str(self.path),
            "exists": True,
            "content": content,
            "sections": {name: sections.get(name, "") for name in order or SECTIONS},
        }

    def memory_write(self, section: str, content: str) -> dict:
        canonical = _canonical_section(section)
        if canonical is None:
            return {"error": f"unknown section '{section}'. Use one of {list(SECTIONS)}"}
        body = str(content or "").strip("\n")
        if len(body) > MAX_CONTENT_CHARS:
            return {"error": f"content too large ({len(body)} chars > {MAX_CONTENT_CHARS})"}

        existing = self.path.read_text(encoding="utf-8", errors="replace") if self.path.exists() else ""
        sections, order = parse(existing) if existing else ({name: "" for name in SECTIONS}, list(SECTIONS))
        sections[canonical] = body
        created = not self.path.exists()

        self.home.mkdir(parents=True, exist_ok=True)
        self.path.write_text(render(sections, order), encoding="utf-8")
        return {
            "path": str(self.path),
            "section": canonical,
            "bytes": len(body),
            "created": created,
        }


def _canonical_section(name: str) -> str | None:
    cleaned = str(name or "").strip().lower()
    for candidate in SECTIONS:
        if candidate.lower() == cleaned:
            return candidate
    return None
