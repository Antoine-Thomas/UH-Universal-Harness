"""What UH remembers, read-only: the wiki, the RAG index, SiYuan, the store.

P3 makes memory visible. That starts with being honest about where it lives:

* **the wiki** is files - ``uhb/hub/wiki/`` in the UHB checkout today, or
  ``$UH_WIKI_DIR`` when someone keeps it elsewhere. UH reads the folder directly:
  no server, no route, no coupling;
* **the RAG index** is ``index.faiss`` + ``chunks.jsonl`` + ``manifeste.json``
  under ``uhb/services/rag/``. UHB exposes **no** HTTP route for it, so this
  module reports what is on disk and says plainly that querying needs the
  service - it never invents a route that would answer 404;
* **SiYuan** is an adapter in UHB, behind no route either: UH reports whether the
  token is configured, not a client it does not have;
* **sessions** are already here, in SQLite with FTS5.

Nothing in this module writes. Every read is bounded (a note is capped, a big
index is not counted), and every path is resolved against the source root before
being returned - a "note name" from a browser is not a licence to read a file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

#: A note larger than this is listed but not returned: it is not a note any more.
MAX_NOTE_BYTES = 200_000
#: Above this, counting the lines of chunks.jsonl costs more than it tells.
MAX_COUNT_BYTES = 20 * 1024 * 1024
NOTE_SUFFIXES = {".md", ".markdown", ".txt"}


class MemoryViewError(RuntimeError):
    """A memory source could not be read. The message is user-facing."""


@dataclass(frozen=True)
class Note:
    """One file of the wiki, as the UI lists it."""

    path: str  # relative to the wiki root, POSIX separators
    name: str
    size: int
    modified: str
    source: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "modified": self.modified,
            "source": self.source,
        }


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _uhb_checkout() -> Optional[Path]:
    from .uhb_spawn import find_checkout

    return find_checkout()


def wiki_roots() -> List[Tuple[str, Path]]:
    """Candidate (label, folder) pairs, most explicit first, existing ones only."""
    candidates: List[Tuple[str, Path]] = []
    configured = (os.environ.get("UH_WIKI_DIR") or "").strip()
    if configured:
        candidates.append(("configured", Path(configured).expanduser()))
    home = Path.home()
    candidates.extend(
        [
            ("local", home / "uh-wiki"),
            ("local", Path.cwd() / "wiki"),
            ("local", Path.cwd() / "llmwiki"),
        ]
    )
    checkout = _uhb_checkout()
    if checkout is not None:
        candidates.append(("uhb", checkout / "uhb" / "hub" / "wiki"))
    return [(label, folder) for label, folder in candidates if folder.is_dir()]


def wiki_root() -> Optional[Tuple[str, Path]]:
    """The first wiki that exists, or ``None`` - a wiki is optional."""
    roots = wiki_roots()
    return roots[0] if roots else None


def _inside(root: Path, candidate: Path) -> bool:
    resolved = candidate.resolve()
    base = root.resolve()
    return resolved == base or base in resolved.parents


def wiki_notes(limit: int = 500) -> List[Note]:
    """Every note of the first wiki, sorted by path. Bounded by ``limit``."""
    found = wiki_root()
    if found is None:
        return []
    label, root = found
    notes: List[Note] = []
    for path in sorted(root.rglob("*")):
        if len(notes) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in NOTE_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        notes.append(
            Note(
                path=path.relative_to(root).as_posix(),
                name=path.name,
                size=stat.st_size,
                modified=_iso(stat.st_mtime),
                source=label,
            )
        )
    return notes


def read_note(relative: str) -> dict:
    """One note of the wiki. Refuses anything outside it, or too large."""
    found = wiki_root()
    if found is None:
        raise MemoryViewError("no wiki found (set UH_WIKI_DIR or add wiki/ to the project)")
    label, root = found
    name = (relative or "").strip().replace("\\", "/")
    if not name:
        raise MemoryViewError("a note name is required")
    candidate = root / name
    if not _inside(root, candidate):
        raise MemoryViewError(f"{name} is outside the wiki")
    if not candidate.is_file():
        raise MemoryViewError(f"unknown note: {name}")
    size = candidate.stat().st_size
    if size > MAX_NOTE_BYTES:
        raise MemoryViewError(f"{name} is {size} bytes; the limit is {MAX_NOTE_BYTES}")
    return {
        "path": candidate.relative_to(root).as_posix(),
        "source": label,
        "size": size,
        "text": candidate.read_text(encoding="utf-8", errors="replace"),
    }


# --------------------------------------------------------------------------- #
# RAG and SiYuan: what is there, and what is missing
# --------------------------------------------------------------------------- #
def _rag_dir() -> Optional[Path]:
    checkout = _uhb_checkout()
    if checkout is None:
        return None
    folder = checkout / "uhb" / "services" / "rag"
    return folder if folder.is_dir() else None


def _count_lines(path: Path) -> Optional[int]:
    """Lines of a text file, or ``None`` when counting would cost too much."""
    try:
        if path.stat().st_size > MAX_COUNT_BYTES:
            return None
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


def rag_state() -> dict:
    """The RAG index on disk, and the honest reason it cannot be queried here."""
    folder = _rag_dir()
    if folder is None:
        return {
            "available": False,
            "reason": "no UHB checkout found: RAG lives in uhb/services/rag",
            "queryable": False,
        }
    index = folder / "index.faiss"
    chunks = folder / "chunks.jsonl"
    manifest = folder / "manifeste.json"
    if not index.is_file():
        return {
            "available": False,
            "reason": (
                "the index is not built: run uhb/services/rag/indexer.py; "
                "UH does not build it for you"
            ),
            "queryable": False,
            "path": str(folder),
        }
    documents = None
    if manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                for key in ("documents", "docs", "sources"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        documents = len(value)
                        break
        except (OSError, ValueError):
            documents = None
    return {
        "available": True,
        "queryable": False,
        "reason": (
            "UHB has no HTTP route for RAG: the index and the model live in "
            "uhb/services/rag/chercher.py, a library. Querying it is a P3 item."
        ),
        "path": str(folder),
        "chunks": _count_lines(chunks),
        "documents": documents,
        "index_mb": round(index.stat().st_size / (1024 * 1024), 1),
        "modified": _iso(index.stat().st_mtime),
    }


def siyuan_state() -> dict:
    """Whether UH could reach SiYuan at all - today, it cannot."""
    token = bool((os.environ.get("SIYUAN_TOKEN") or "").strip())
    url = (os.environ.get("SIYUAN_URL") or "").strip()
    return {
        "available": False,
        "configured": bool(token and url),
        "reason": (
            "SiYuan is read by a UHB adapter; UHB exposes no route for it, and UH "
            "does not speak SiYuan directly. Setting SIYUAN_URL and SIYUAN_TOKEN "
            "is necessary, not sufficient."
        ),
        "url": url or None,
    }


def memory_file() -> dict:
    """UH's own MEMORY.md: sections, size, presence."""
    from .toolsets.memory import SECTIONS, memory_path, parse

    path = memory_path()
    if not path.is_file():
        return {"available": False, "path": str(path), "sections": list(SECTIONS)}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"available": False, "path": str(path), "reason": str(exc)}
    parsed, order = parse(text) if text.strip() else ({}, [])
    return {
        "available": True,
        "path": str(path),
        "size": len(text.encode("utf-8")),
        "sections": list(parsed),
        "order": order,
        "bytes_by_section": {
            name: len(body.encode("utf-8")) for name, body in parsed.items() if body
        },
    }


def sources() -> dict:
    """Everything the Memory tab needs, in one round trip."""
    found = wiki_root()
    return {
        "wiki": {
            "available": found is not None,
            "label": found[0] if found else None,
            "path": str(found[1]) if found else None,
            "notes": len(wiki_notes()),
            "reason": None if found else "no wiki found (set UH_WIKI_DIR or add wiki/)",
        },
        "memory": memory_file(),
        "rag": rag_state(),
        "siyuan": siyuan_state(),
    }


# --------------------------------------------------------------------------- #
# search across what is readable
# --------------------------------------------------------------------------- #
def _snippet(text: str, needle: str, width: int = 160) -> str:
    lowered = text.lower()
    at = lowered.find(needle.lower())
    if at < 0:
        return " ".join(text[:width].split())
    start = max(0, at - width // 3)
    chunk = " ".join(text[start : start + width].split())
    return ("… " if start else "") + chunk + (" …" if start + width < len(text) else "")


def search_memory(query: str, limit: int = 20) -> List[dict]:
    """Substring search in MEMORY.md and the wiki. Case-insensitive, bounded."""
    needle = (query or "").strip()
    if not needle:
        return []
    hits: List[dict] = []
    memory = memory_file()
    if memory.get("available"):
        try:
            text = Path(str(memory["path"])).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if needle.lower() in text.lower():
            hits.append(
                {
                    "kind": "memory",
                    "title": "MEMORY.md",
                    "path": str(memory["path"]),
                    "snippet": _snippet(text, needle),
                }
            )
    for note in wiki_notes(limit=500):
        if len(hits) >= limit:
            break
        found = wiki_root()
        assert found is not None
        try:
            text = (found[1] / note.path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle.lower() in text.lower():
            hits.append(
                {
                    "kind": "wiki",
                    "title": note.path,
                    "path": str(found[1] / note.path),
                    "snippet": _snippet(text, needle),
                }
            )
    return hits[:limit]


__all__ = [
    "MAX_NOTE_BYTES",
    "Note",
    "MemoryViewError",
    "memory_file",
    "rag_state",
    "read_note",
    "search_memory",
    "siyuan_state",
    "sources",
    "wiki_notes",
    "wiki_root",
    "wiki_roots",
]
