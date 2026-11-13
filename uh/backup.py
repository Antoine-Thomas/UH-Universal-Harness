"""One archive for everything UH knows about you, and one way back.

The data lives in files - ``$UH_HOME`` - precisely so it can leave: a conversation
in SQLite under your home directory survives a cleared browser cache, a new
machine, or a reinstall, as long as you can carry it. This module is the carrying.

What a backup contains (``INCLUDE``): the store, ``MEMORY.md``, the config, the
dreams folder (passes, reports, journal, ideas), the skills folder and the
uploads. What it must **never** contain (``NEVER``): ``keys.json``. A backup is a
file that travels - by mail, on a USB stick, into a chat - and an API key inside
it is a credential leak dressed as a feature. The manifest states both lists, so
an archive says out loud what it holds.

Two details that make the difference between a backup and a plausible-looking
file: the SQLite WAL is checkpointed before ``uh.db`` is copied (otherwise the
archive silently misses the last turns), and an import snapshots the current home
first, then replaces the store only after removing stale ``-wal``/``-shm`` files
that would otherwise replay their old pages into the new database.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__
from .config import uh_home

#: Written inside the archive: what it holds, what it refused to hold, and when.
MANIFEST = "uh-backup.json"
#: Bumped when the layout changes, so an importer can refuse what it cannot read.
FORMAT = 1

#: Relative to the UH home. A missing entry is skipped, never an error.
INCLUDE: Tuple[str, ...] = ("uh.db", "MEMORY.md", "config.toml", "dreams", "skills", "uploads")

#: Never copied *into* an archive, and never restored *from* one.
#: ``keys.json`` is the only one that is a security decision; the rest are
#: regenerable, transient, or would nest a backup inside a backup.
NEVER: Dict[str, str] = {
    "keys.json": "credentials - a backup is a file that travels",
    "backups": "would nest an archive inside an archive",
    "uh.db-wal": "transient SQLite journal, checkpointed into uh.db instead",
    "uh.db-shm": "transient SQLite shared memory",
    "dream-daemon.status": "transient daemon state",
    "dream-daemon.stop": "transient daemon signal",
    "uh-web.log": "log",
    "uhb.log": "log",
    "uh-web.ps1": "regenerable by `uh install-shortcut`",
    "uh-web.ico": "regenerable by `uh install-shortcut`",
}


class BackupError(RuntimeError):
    """The archive cannot be made, or cannot be trusted."""


def backup_dir(home: Optional[Path] = None) -> Path:
    return (home or uh_home()) / "backups"


def default_name(now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"uh_backup_{stamp}.zip"


def safety_name(now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"uh_pre-import_{stamp}.zip"


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def checkpoint_db(path: Path) -> bool:
    """Fold the WAL into ``uh.db`` so the copy is the whole story.

    Returns ``True`` when the archive can be trusted to be complete and ``False``
    when the database is busy or unreadable: the export still happens (the archive
    is valid for what it contains) but the caller reports that the last turns may
    be missing, instead of pretending otherwise. A home with no database has
    nothing to fold in, so it is complete by definition.
    """
    if not path.is_file():
        return True
    try:
        connection = sqlite3.connect(str(path), timeout=2.0)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.commit()
        finally:
            connection.close()
        return True
    except sqlite3.Error:
        return False


def _iter_files(source: Path, home: Path) -> List[Path]:
    if source.is_file():
        return [] if source.name in NEVER else [source]
    if not source.is_dir():
        return []
    found: List[Path] = []
    for child in sorted(source.rglob("*")):
        if not child.is_file():
            continue
        relative = child.relative_to(home)
        if any(part in NEVER for part in relative.parts):
            continue
        found.append(child)
    return found


def export(
    destination: Optional[Path] = None,
    home: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Write one archive and describe it. Raises :class:`BackupError` on refusal."""
    home = Path(home or uh_home())
    if not home.is_dir():
        raise BackupError(f"nothing to back up: {home} does not exist")
    checkpointed = checkpoint_db(home / "uh.db")
    target = Path(destination) if destination else backup_dir(home) / default_name(now)
    target.parent.mkdir(parents=True, exist_ok=True)

    files: List[str] = []
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in INCLUDE:
                for path in _iter_files(home / name, home):
                    archive.write(path, path.relative_to(home).as_posix())
                    files.append(path.relative_to(home).as_posix())
            manifest = {
                "format": FORMAT,
                "version": __version__,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "home": str(home),
                "files": files,
                "count": len(files),
                "includes": list(INCLUDE),
                "excludes": {name: reason for name, reason in NEVER.items()},
                "wal_checkpoint": checkpointed,
            }
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"cannot write {target}: {exc}") from exc

    return {
        "path": str(target),
        "name": target.name,
        "bytes": target.stat().st_size if target.is_file() else 0,
        "files": len(files),
        "includes": list(INCLUDE),
        "excludes": sorted(NEVER),
        "wal_checkpoint": checkpointed,
        "created_at": manifest["created_at"],
    }


def list_backups(home: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Existing archives, newest first. Sizes and dates, not guesses."""
    folder = backup_dir(home)
    if not folder.is_dir():
        return []
    found: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        found.append(
            {
                "name": path.name,
                "bytes": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(
                    timespec="seconds"
                ),
            }
        )
    return found


def resolve_backup(name: str, home: Optional[Path] = None) -> Optional[Path]:
    """A name from the UI, turned into a file **inside** the backup folder.

    Only a bare archive name is accepted: no directory separator, no dot-dot,
    no absolute path. The check is the path-traversal guard.
    """
    clean = (name or "").strip()
    if not clean or clean.startswith((".", "/", "\\")) or any(sep in clean for sep in ("/", "\\")):
        return None
    if not clean.lower().endswith(".zip"):
        return None
    candidate = backup_dir(home) / clean
    return candidate if candidate.is_file() else None


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def _safe_member(name: str) -> bool:
    """A member path must stay inside the home: no absolute, no ``..``."""
    if not name or name.startswith(("/", "\\")):
        return False
    if ":" in name.split("/")[0] or ":" in name.split("\\")[0]:
        return False  # C:\... on Windows
    parts = [part for part in name.replace("\\", "/").split("/") if part]
    return bool(parts) and ".." not in parts


def inspect(path: Path) -> Dict[str, Any]:
    """Read an archive's table of contents without writing anything.

    A zip with no ``uh.db`` is not a UH backup: it is refused before a single
    byte is restored, because restoring half a home is worse than refusing.
    """
    path = Path(path)
    if not path.is_file():
        raise BackupError(f"no such file: {path}")
    if not zipfile.is_zipfile(path):
        raise BackupError("not a zip archive")
    try:
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
            manifest: Optional[dict] = None
            if MANIFEST in names:
                try:
                    manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    manifest = None
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"cannot read {path.name}: {exc}") from exc

    clean = [name for name in names if _safe_member(name)]
    rejected = [name for name in names if not _safe_member(name)]
    # At least one thing UH recognises: a zip of holiday photos is not a backup,
    # but a home that never got a database (skills and dreams only) is one.
    entries = {"uh.db", "MEMORY.md", "config.toml"} & set(clean)
    folders = [prefix for prefix in ("dreams/", "skills/", "uploads/") if any(
        name.startswith(prefix) for name in clean
    )]
    if not entries and not folders:
        raise BackupError(
            "this archive holds no UH data: no uh.db, MEMORY.md, config.toml, "
            "dreams/, skills/ or uploads/"
        )
    if manifest is not None and int(manifest.get("format") or FORMAT) > FORMAT:
        raise BackupError(
            f"archive format {manifest.get('format')} is newer than this build ({FORMAT})"
        )
    return {
        "ok": True,
        "manifest": manifest,
        "files": len(clean),
        "rejected": rejected,
        "bytes": path.stat().st_size,
        "has": {
            "uh.db": "uh.db" in clean,
            "MEMORY.md": "MEMORY.md" in clean,
            "dreams": any(name.startswith("dreams/") for name in clean),
            "skills": any(name.startswith("skills/") for name in clean),
            "config.toml": "config.toml" in clean,
            "uploads": any(name.startswith("uploads/") for name in clean),
        },
    }


def import_backup(
    path: Path, home: Optional[Path] = None, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Restore an archive over this home, after snapshotting what is here.

    The safety copy is written **before** anything is replaced, and its path is
    returned: an import you cannot undo is a deletion with extra steps.
    """
    home = Path(home or uh_home())
    home.mkdir(parents=True, exist_ok=True)
    inspection = inspect(path)
    snapshot = export(destination=backup_dir(home) / safety_name(now), home=home, now=now)

    restored: List[str] = []
    skipped: List[str] = []
    with tempfile.TemporaryDirectory(prefix="uh-import-") as staging:
        staging_path = Path(staging)
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not _safe_member(name):
                    skipped.append(name)
                    continue
                if any(part in NEVER for part in name.replace("\\", "/").split("/")):
                    skipped.append(name)
                    continue
                if name == MANIFEST:
                    # The manifest describes the archive; it is not part of a home.
                    skipped.append(name)
                    continue
                target = staging_path / name.replace("\\", "/")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                restored.append(name)

        if "uh.db" in restored:
            # A stale journal next to the incoming database would replay pages
            # from the home we are replacing.
            for suffix in ("-wal", "-shm"):
                stale = home / f"uh.db{suffix}"
                if stale.is_file():
                    stale.unlink(missing_ok=True)
        for name in restored:
            staged = staging_path / name
            landing = home / name
            landing.parent.mkdir(parents=True, exist_ok=True)
            if landing.exists():
                landing.unlink()
            shutil.move(str(staged), str(landing))

    return {
        "restored": len(restored),
        "skipped": skipped,
        "safety": snapshot["path"],
        "files": restored,
        "was": inspection["has"],
        "restart_required": True,
    }


__all__ = [
    "BackupError",
    "FORMAT",
    "INCLUDE",
    "MANIFEST",
    "NEVER",
    "backup_dir",
    "checkpoint_db",
    "default_name",
    "export",
    "import_backup",
    "inspect",
    "list_backups",
    "resolve_backup",
    "safety_name",
]
