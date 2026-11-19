"""P1-13: what was judged good is kept, compressed without losing what matters.

The promise is narrow and testable: an image or a voice clip the operator marked
« Garder » (or voted « Utile ») is copied into ``$UH_HOME/memory`` in a canonical
format - **PNG lossless** for a picture (full resolution, so a 4K comes back as 4K),
**FLAC 24-bit stereo** for a sound - indexed by its SHA-256, and tied to the message
it came from.

Why a copy and not a pointer: the run directory is retention's business (P1-12) and a
trashed file must not take the memory with it. Why the canonical format: a 40 MB PNG
of a screenshot is the same picture as a 900 KB WebP, but only one of them can be
re-developed at full quality later, and the roadmap asked for the second property.

Nothing here is imported by the chat turn: memory is written by an action, read by a
panel.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List

#: Where the kept copies live, beside the index.
MEMORY_DIR = "memory"
INDEX_FILE = "memory-index.jsonl"


class MemoryError(RuntimeError):
    """A refusal the interface shows: nothing was kept."""


def memory_root() -> Path:
    import os

    return Path(os.environ.get("UH_HOME") or (Path.home() / ".uh")) / MEMORY_DIR


def index_path() -> Path:
    import os

    return Path(os.environ.get("UH_HOME") or (Path.home() / ".uh")) / INDEX_FILE


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _to_png(source: Path, target: Path) -> Dict[str, Any]:
    from PIL import Image

    with Image.open(source) as opened:
        image = opened.convert("RGB") if opened.mode not in {"RGB", "RGBA", "L"} else opened
        image.save(target, format="PNG", optimize=True)
        width, height = image.size
    return {"format": "png", "width": width, "height": height}


def _to_flac(source: Path, target: Path) -> Dict[str, Any]:
    from .audio import ffmpeg_path

    binary = ffmpeg_path()
    if not binary:
        raise MemoryError("ffmpeg absent : conservation FLAC 24 bits impossible")
    import subprocess

    proc = subprocess.run(
        [binary, "-y", "-i", str(source), "-c:a", "flac", "-sample_fmt", "s32",
         "-ar", "48000", "-ac", "2", str(target)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if proc.returncode != 0 or not target.is_file():
        raise MemoryError(f"ffmpeg a refusé la conversion : {proc.stderr[-160:]}")
    try:
        from .audio import seconds_of

        seconds = seconds_of(target)
    except Exception:  # noqa: BLE001
        seconds = 0.0
    return {"format": "flac", "seconds": seconds, "bits": 24, "channels": 2}


def keep(
    source: Path,
    *,
    kind: str = "image",
    session_id: str = "",
    message_id: Any = None,
    note: str = "",
) -> Dict[str, Any]:
    """Copy one file into the memory in its canonical format and index it.

    Idempotent by content: keeping the same bytes twice returns the existing entry
    instead of a second copy. That is what makes « Garder » safe to click twice.
    """
    path = Path(source)
    if not path.is_file():
        raise MemoryError("fichier introuvable : rien n'a été conservé")
    digest = _sha256(path)
    folder = memory_root() / kind
    folder.mkdir(parents=True, exist_ok=True)

    if kind == "audio":
        target = folder / f"{digest}.flac"
        facts: Dict[str, Any] = {}
        if not target.is_file():
            facts = _to_flac(path, target)
        else:
            facts = {"format": "flac", "bits": 24, "channels": 2}
    else:
        target = folder / f"{digest}.png"
        if not target.is_file():
            facts = _to_png(path, target)
        else:
            # Already kept: the entry still has to say what it is worth. A re-keep that
            # returned no dimensions made the interface show "None" (gate B, line 5.2).
            from PIL import Image

            with Image.open(target) as opened:
                facts = {"format": "png", "width": opened.width, "height": opened.height}

    entry = {
        "sha256": digest,
        "kind": kind,
        "path": str(target),
        "bytes": target.stat().st_size,
        "source": str(path),
        "session": str(session_id or ""),
        "message_id": message_id,
        "note": str(note or "")[:200],
        "kept_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **facts,
    }
    # The digest of the copy is its identity on disk; the source digest is what the
    # operator gave us. Both, because a re-encode is lossless in pixels and not in
    # bytes - checking "the SHA-256" against the wrong one is a false alarm (gate B).
    entry["sha256_stored"] = _sha256(target)
    _append(entry)
    return entry


def _append(entry: Dict[str, Any]) -> None:
    target = index_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def entries() -> List[Dict[str, Any]]:
    """The index, last line per SHA-256 winning (a re-keep updates the note)."""
    target = index_path()
    if not target.is_file():
        return []
    latest: Dict[str, Dict[str, Any]] = {}
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("sha256"):
            latest[str(entry["sha256"])] = entry
    return sorted(latest.values(), key=lambda item: str(item.get("kept_at") or ""), reverse=True)


def drop_for(source: Path) -> int:
    """Forget every entry made from this file - the bin speaks for the memory too.

    Called when the trash is emptied (not when a file enters it: the undo would
    otherwise kill the memory of something the operator just got back).
    """
    wanted = str(Path(source))
    kept = [entry for entry in entries() if str(entry.get("source") or "") != wanted]
    removed = 0
    for entry in entries():
        if str(entry.get("source") or "") != wanted:
            continue
        path = Path(str(entry.get("path") or ""))
        if path.is_file():
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    if removed or len(kept) != len(entries()):
        target = index_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in kept),
            encoding="utf-8",
        )
    return removed


def weight() -> Dict[str, Any]:
    """How much the memory costs, by kind - the number the panel shows."""
    root = memory_root()
    total = 0
    by_kind: Dict[str, int] = {}
    if root.is_dir():
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            size = sum(item.stat().st_size for item in folder.glob("*") if item.is_file())
            by_kind[folder.name] = size
            total += size
    return {"root": str(root), "bytes": total, "by_kind": by_kind, "entries": len(entries())}


__all__ = ["INDEX_FILE", "MEMORY_DIR", "MemoryError", "drop_for", "entries", "index_path", "keep", "memory_root", "weight"]
