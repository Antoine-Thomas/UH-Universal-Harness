"""P1-12: the interface cleans up after itself, and repairs what it left behind.

Two jobs, and the second one is the one that gets forgotten:

* **sweep** - delete the prepared copies of finished turns (``derived/``), empty
  folders, and anything under ``uploads`` older than a week. The files the operator
  chose are never swept: a ``.keep`` sidecar is the border.
* **repair** - report the two ways a disk and a database drift apart: a message whose
  artifact points at a file that is gone, and a file in a session folder that no
  message mentions. The first is shown, never silently deleted (the operator may
  still want the text); the second is reported with its weight, and deleted only when
  the caller asks for it explicitly.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

#: A file nobody kept and nobody mentioned for this long is clutter.
STALE_DAYS = 7


def _age_days(path: Path) -> float:
    try:
        return (time.time() - path.stat().st_mtime) / 86400.0
    except OSError:
        return 0.0


def _kept(path: Path) -> bool:
    return path.with_name(path.name + ".keep").is_file()


def sweep_folder(session_dir: Path, *, stale_days: float = STALE_DAYS) -> Dict[str, Any]:
    """Remove prepared copies, empty folders and stale uploads in one session."""
    from . import media as uh_media

    removed_derived = uh_media.purge(session_dir) if Path(session_dir).is_dir() else 0
    removed_stale = 0
    freed = 0
    for item in sorted(Path(session_dir).rglob("*")):
        if not item.is_file() or _kept(item):
            continue
        if "derived" in item.parts:
            continue
        if _age_days(item) < stale_days:
            continue
        try:
            freed += item.stat().st_size
            item.unlink()
            removed_stale += 1
        except OSError:
            continue
    for folder in sorted(Path(session_dir).rglob("*"), reverse=True):
        if folder.is_dir() and not any(folder.iterdir()):
            try:
                folder.rmdir()
            except OSError:
                pass
    return {"derived": removed_derived, "stale": removed_stale, "freed_bytes": freed}


def referenced_paths(rows: List[Dict[str, Any]]) -> set[str]:
    """Every file path the stored thread mentions (artifact records and inline notes)."""
    found: set[str] = set()
    for row in rows:
        artifacts = row.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                source = artifact.get("source") if isinstance(artifact, dict) else None
                if isinstance(source, dict) and source.get("path"):
                    found.add(str(source["path"]))
                if isinstance(artifact, dict) and artifact.get("path"):
                    found.add(str(artifact["path"]))
        content = str(row.get("content") or "")
        if "C:\\" in content or "/" in content:
            for token in content.replace("\n", " ").split(" "):
                if (":" in token or token.startswith("/")) and ("\\" in token or "/" in token):
                    found.add(token.strip(",;()[]{}\"'"))
    return found


def report(session_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Orphans, both ways - what the panel shows instead of a silent drift."""
    folder = Path(session_dir)
    mentioned = referenced_paths(rows)
    on_disk: List[Path] = []
    if folder.is_dir():
        on_disk = [
            item
            for item in folder.rglob("*")
            if item.is_file() and "derived" not in item.parts and not item.name.endswith(".keep")
        ]
    unmentioned = [item for item in on_disk if str(item) not in mentioned]
    missing = sorted(path for path in mentioned if path.startswith(str(folder)) and not Path(path).is_file())
    return {
        "files": len(on_disk),
        "unmentioned": [
            {"path": str(item), "bytes": item.stat().st_size} for item in unmentioned
        ],
        "missing": missing[:50],
        "unmentioned_bytes": sum(item.stat().st_size for item in unmentioned),
    }


def clean_unmentioned(session_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Delete the files no message mentions - only when the caller asks for it."""
    data = report(session_dir, rows)
    removed = 0
    freed = 0
    for item in data["unmentioned"]:
        path = Path(item["path"])
        if _kept(path):
            continue
        try:
            freed += path.stat().st_size
            path.unlink()
            removed += 1
        except OSError:
            continue
    return {"removed": removed, "freed_bytes": freed}


def guard_path(raw: str, root: Path) -> bool:
    """A cheap containment check for a path that came from the browser."""
    try:
        real = Path(os.path.realpath(raw))
        real.relative_to(Path(os.path.realpath(root)))
    except (OSError, ValueError):
        return False
    return True


def summary(session_dir: Path, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """One object for ``GET /api/maintenance``: what is here, what is wrong."""
    data = report(session_dir, rows)
    return {
        "session_dir": str(session_dir),
        "files": data["files"],
        "unmentioned": len(data["unmentioned"]),
        "unmentioned_bytes": data["unmentioned_bytes"],
        "missing": len(data["missing"]),
        "stale_days": STALE_DAYS,
    }


def encode(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


__all__ = [
    "STALE_DAYS",
    "clean_unmentioned",
    "guard_path",
    "referenced_paths",
    "report",
    "summary",
    "sweep_folder",
]
