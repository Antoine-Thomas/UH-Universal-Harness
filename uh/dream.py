"""Dream journal and snapshots: what makes an autonomous pass reversible.

P4 will let a pass write while nobody is watching. Two things have to exist
before the first automatic write: a record of what changed, and a way back.

* the **journal** is one JSONL file per day under ``$UH_HOME/dreams/``. One line
  per action, appended, never rewritten: a log that needs its own log is not a
  log. It rotates at 1000 lines because a file nobody can open is a file nobody
  reads;
* the **snapshot** copies the files a dream pass may touch - ``wiki/``, ``docs/``
  and ``MEMORY.md`` - into ``$UH_HOME/dreams/snapshot-<stamp>/``, with a manifest
  that records the original absolute path of each file. ``uh/``, ``tests/``,
  ``pyproject.toml`` and ``.git/`` are never copied and never restored: the dream
  pass proposes changes to knowledge, it does not patch its own code.

Rollback restores what the manifest lists and **creates a snapshot of the current
state first**, so a rollback of the rollback exists. It never deletes a file that
the snapshot does not know about: restoring is one thing, pruning is another, and
the pass that wants to delete will journal it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

from .config import uh_home

#: Directories a snapshot may copy, relative to the workspace root.
ALLOWED_DIRS = ("wiki", "docs")
#: Files a snapshot may copy, relative to the workspace root.
ALLOWED_FILES = ("MEMORY.md",)
#: Directories that are never copied nor restored. ``uh/`` and ``tests/`` are the
#: code, ``.git/`` is history, and a dream pass that can rewrite any of the three
#: is not a dream pass any more.
EXCLUDED = (".git", "uh", "tests", "pyproject.toml")

#: One day of actions. Past this, the day is archived as ``journal-<date>.1``.
MAX_LINES = 1000
#: A snapshot reference: ``snapshot-YYYYMMDD-HHMMSS``.
SNAPSHOT_PREFIX = "snapshot-"


class DreamError(RuntimeError):
    """A dream operation cannot proceed. The message is meant for a human."""


# --------------------------------------------------------------------------- #
# journal
# --------------------------------------------------------------------------- #
def dreams_dir() -> Path:
    return uh_home() / "dreams"


def _today() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%d")


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class DreamJournal:
    """Append-only JSONL journal, one file per day, rotating at ``MAX_LINES``."""

    def __init__(self, directory: Optional[Path] = None, max_lines: int = MAX_LINES) -> None:
        self.directory = Path(directory) if directory is not None else dreams_dir()
        self.max_lines = max(2, int(max_lines))

    # -- paths ------------------------------------------------------------- #
    def file_for(self, date: Optional[str] = None) -> Path:
        """``journal-<YYYYMMDD>.jsonl``; ``date`` accepts ``YYYYMMDD`` or ISO."""
        day = (date or _today()).replace("-", "")
        return self.directory / f"journal-{day}.jsonl"

    def dates(self) -> List[str]:
        """Every day this journal has, oldest first."""
        if not self.directory.is_dir():
            return []
        found = []
        for path in self.directory.glob("journal-*.jsonl"):
            day = path.stem.replace("journal-", "")
            if len(day) == 8 and day.isdigit():
                found.append(day)
        return sorted(found)

    # -- write ------------------------------------------------------------- #
    def append(self, action: dict) -> None:
        """Add one action. ``timestamp`` and the six fields are always present."""
        entry = {
            "timestamp": str(action.get("timestamp") or _now_iso()),
            "type": str(action.get("type") or "write"),
            "path": str(action.get("path") or ""),
            "before_hash": action.get("before_hash"),
            "after_hash": action.get("after_hash"),
            "bytes_delta": int(action.get("bytes_delta") or 0),
            "reason": str(action.get("reason") or ""),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = str(entry["timestamp"])
        # An ISO timestamp carries its own day: a late pass on the 1st must not
        # write into the 2nd because the clock rolled over mid-run.
        target = self.file_for(stamp[:10] if "-" in stamp else None)
        if self._lines(target) >= self.max_lines:
            target = self._rotate(target)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _lines(self, path: Path) -> int:
        if not path.is_file():
            return 0
        try:
            with path.open("rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0

    def _rotate(self, path: Path) -> Path:
        """Move the full day aside and start a new one (``.1``, ``.2``, ...)."""
        index = 1
        while True:
            archive = path.with_suffix(f".{index}.jsonl")
            if not archive.exists():
                break
            index += 1
        try:
            path.rename(archive)
        except OSError as exc:
            raise DreamError(f"cannot rotate {path}: {exc}") from exc
        return path

    # -- read -------------------------------------------------------------- #
    def read(self, date: Optional[str] = None) -> List[dict]:
        """The actions of one day, oldest first. Bad lines are skipped, not fatal."""
        path = self.file_for(date)
        if not path.is_file():
            return []
        entries: List[dict] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if isinstance(payload, dict):
                    entries.append(payload)
        return entries


# --------------------------------------------------------------------------- #
# hashes and file inventory
# --------------------------------------------------------------------------- #
def hash_file(path: Path) -> Optional[str]:
    """SHA-256 of a file, ``None`` when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _allowed_roots(root: Path) -> List[Path]:
    """The directories whose contents a snapshot may copy (existing ones)."""
    roots = [root / name for name in ALLOWED_DIRS]
    return [path for path in roots if path.is_dir()]


def _allowed_files(root: Path) -> List[Path]:
    found = [root / name for name in ALLOWED_FILES if (root / name).is_file()]
    memory = uh_home() / "MEMORY.md"
    if memory.is_file() and memory not in found:
        found.append(memory)
    return found


def _iter_sources(root: Path) -> Iterator[Path]:
    for folder in _allowed_roots(root):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and not _is_excluded(root, path):
                yield path
    for path in _allowed_files(root):
        yield path


def _is_excluded(root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True  # outside the workspace: not ours to copy
    parts = relative.parts
    if not parts:
        return True
    # Only the top-level name decides: a note called ``wiki/tests.md`` is a note,
    # not the test suite.
    return parts[0] in EXCLUDED


def is_restorable(root: Path, path: Path) -> bool:
    """Whether ``path`` is inside a tree a dream pass owns.

    Checked again at restore time, against the manifest: a snapshot written by an
    older version, or edited by hand, must not become a way to overwrite ``uh/``.
    """
    resolved = Path(path).resolve()
    for folder in _allowed_roots(root):
        if folder.resolve() in resolved.parents:
            return True
    for candidate in _allowed_files(root):
        if candidate.resolve() == resolved:
            return True
    return False


# --------------------------------------------------------------------------- #
# snapshots
# --------------------------------------------------------------------------- #
def create_snapshot(root: Optional[Path] = None, journal: Optional[DreamJournal] = None) -> Path:
    """Copy ``wiki/``, ``docs/`` and ``MEMORY.md`` under ``dreams/snapshot-<stamp>/``."""
    base = Path(root or Path.cwd()).resolve()
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot = dreams_dir() / f"{SNAPSHOT_PREFIX}{stamp}"
    suffix = 1
    while snapshot.exists():  # two snapshots inside one second
        suffix += 1
        snapshot = dreams_dir() / f"{SNAPSHOT_PREFIX}{stamp}-{suffix}"
    tree = snapshot / "tree"
    tree.mkdir(parents=True, exist_ok=True)

    entries: List[dict] = []
    for index, source in enumerate(_iter_sources(base)):
        stored = f"{index:04d}"
        try:
            shutil.copy2(source, tree / stored)
        except OSError as exc:
            raise DreamError(f"cannot copy {source}: {exc}") from exc
        entries.append(
            {
                "original": str(source),
                "relative": _relative_label(base, source),
                "size": source.stat().st_size,
                "sha256": hash_file(source),
                "stored": stored,
            }
        )

    manifest = {
        "id": snapshot.name,
        "created": _now_iso(),
        "root": str(base),
        "files": entries,
        "total_bytes": sum(int(entry["size"]) for entry in entries),
    }
    try:
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        raise DreamError(f"cannot write the manifest in {snapshot}: {exc}") from exc
    if journal is not None:
        journal.append(
            {
                "type": "create",
                "path": str(snapshot),
                "bytes_delta": sum(int(entry["size"]) for entry in entries),
                "reason": f"snapshot of {len(entries)} file(s)",
            }
        )
    return snapshot


def _relative_label(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)  # MEMORY.md lives in UH_HOME, outside the workspace


def _read_manifest(snapshot: Path) -> dict:
    manifest = snapshot / "manifest.json"
    if not manifest.is_file():
        raise DreamError(f"{snapshot.name} has no manifest: not a snapshot this tool wrote")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DreamError(f"cannot read {manifest}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise DreamError(f"{manifest} is not a manifest this tool understands")
    return payload


def list_snapshots() -> List[dict]:
    """Every snapshot, newest first: id, date, files, bytes."""
    folder = dreams_dir()
    if not folder.is_dir():
        return []
    found: List[dict] = []
    for path in sorted(folder.glob(f"{SNAPSHOT_PREFIX}*"), reverse=True):
        if not path.is_dir():
            continue
        try:
            manifest = _read_manifest(path)
        except DreamError:
            found.append({"id": path.name, "created": None, "files": None, "bytes": None})
            continue
        found.append(
            {
                "id": path.name,
                "created": manifest.get("created"),
                "files": len(manifest["files"]),
                "bytes": sum(int(item.get("size") or 0) for item in manifest["files"]),
            }
        )
    return found


def _find_snapshot(snapshot_id: Optional[str]) -> Path:
    folder = dreams_dir()
    if snapshot_id:
        wanted = snapshot_id if snapshot_id.startswith(SNAPSHOT_PREFIX) else f"{SNAPSHOT_PREFIX}{snapshot_id}"
        candidate = folder / wanted
        if not candidate.is_dir():
            raise DreamError(f"unknown snapshot: {snapshot_id}")
        return candidate
    available = [path for path in sorted(folder.glob(f"{SNAPSHOT_PREFIX}*"), reverse=True) if path.is_dir()]
    if not available:
        raise DreamError(f"no snapshot in {folder}: run `uh dream --snapshot` first")
    return available[0]


def rollback(
    snapshot_id: Optional[str] = None,
    root: Optional[Path] = None,
    journal: Optional[DreamJournal] = None,
) -> int:
    """Restore the files of a snapshot, after snapshotting the current state.

    Returns the number of files restored. Files that the snapshot does not list
    are left alone: restoring is not pruning.
    """
    snapshot = _find_snapshot(snapshot_id)
    manifest = _read_manifest(snapshot)
    base = Path(root or manifest.get("root") or Path.cwd()).resolve()

    # The undo of the undo: captured before a single byte is overwritten.
    safety = create_snapshot(base, journal=None)

    restored = 0
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            continue
        original = Path(str(entry.get("original") or ""))
        stored = snapshot / "tree" / str(entry.get("stored") or "")
        if not original.name or not stored.is_file():
            continue
        if not is_restorable(base, original):
            # A manifest that points outside the allowed trees is a corrupt or
            # hand-edited snapshot: skip it, and say so on the journal.
            if journal is not None:
                journal.append(
                    {
                        "type": "write",
                        "path": str(original),
                        "reason": f"skipped: outside the trees a dream pass owns ({snapshot.name})",
                    }
                )
            continue
        before = hash_file(original)
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stored, original)
        except OSError:
            continue
        after = hash_file(original)
        restored += 1
        if journal is not None:
            try:
                delta = original.stat().st_size
            except OSError:
                delta = 0
            journal.append(
                {
                    "type": "write",
                    "path": str(original),
                    "before_hash": before,
                    "after_hash": after,
                    "bytes_delta": delta,
                    "reason": f"rollback {snapshot.name} (safety copy: {safety.name})",
                }
            )
    return restored


__all__ = [
    "ALLOWED_DIRS",
    "ALLOWED_FILES",
    "DreamError",
    "DreamJournal",
    "EXCLUDED",
    "MAX_LINES",
    "create_snapshot",
    "dreams_dir",
    "hash_file",
    "is_restorable",
    "list_snapshots",
    "rollback",
]
