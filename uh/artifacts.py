"""Keep, trash or rate one image a tool produced.

The transcript shows a figure from its data URL, but the bytes on disk are the
tool's real output, and "Poubelle" has to delete something that exists. So the
image payload carries a *provenance* (the file UHB wrote) that stays server-side:
it is never sent to the model, and never handed to the browser as a path to fetch.

The rule that makes this safe is the guard in :func:`resolve`: an action is only
ever applied to a file that resolves **inside UHB's runs root**. A tool message is
data the model wrote; it is not a capability to delete any file the operator owns.
A provenance that points elsewhere (a hand-written `../../Documents/...`, a
symlink out) is refused, not repaired.

Three actions, three meanings:

* ``keep`` writes a ``<file>.keep`` sidecar. It moves nothing: the transcript and
  the run directory keep working, and the retention policy (P1-12) can read the
  sidecars to spare what the operator chose to keep.
* ``trash`` moves the file into ``<runs>/_trash/<run>/`` and writes a ``.json``
  sidecar with its origin, so the move is reversible by hand and traceable.
* ``rate`` appends one line to ``$UH_HOME/artifact-ratings.jsonl``. Local file
  first, best-effort delivery later (P1-13): a vote must never be lost because a
  second brain is offline.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

#: Where UHB writes a run. Overridable for a test, never for a model.
RUNS_ROOT = Path(os.environ.get("UH_UHB_RUNS") or (Path.home() / ".uhb" / "runs"))
RATINGS_FILE = Path(os.environ.get("UH_HOME") or (Path.home() / ".uh")) / "artifact-ratings.jsonl"
TRASH_DIR = "_trash"
VOTES = ("up", "down")
#: The retraction of a vote. Recorded, not deleted: the journal is a history, and
#: "the operator changed their mind" is a fact worth keeping (P1-13 will want it).
VOTE_CLEAR = "none"
KEEP_SUFFIX = ".keep"


class ArtifactActionError(ValueError):
    """A refusal the browser can show: nothing was touched."""


def runs_root() -> Path:
    return Path(RUNS_ROOT).expanduser()


def _resolved_inside(path: Path) -> Optional[Path]:
    """The file's real path when it lives under the runs root, else None.

    ``realpath`` first: a symlink inside the tree that points outside is out.
    """
    try:
        real = Path(os.path.realpath(path))
    except OSError:
        return None
    root = Path(os.path.realpath(runs_root()))
    try:
        real.relative_to(root)
    except ValueError:
        return None
    return real


def resolve(provenance: Dict[str, Any]) -> Path:
    """The file an artifact points at, or a refusal.

    Accepts ``{"path": ...}`` or ``{"run_dir": ..., "relative": ...}``. Both are
    checked, never trusted: a relative part with ``..`` in it is refused by the
    same ``relative_to`` as an absolute path outside the root.
    """
    raw = str((provenance or {}).get("path") or "").strip()
    if not raw:
        run_dir = str((provenance or {}).get("run_dir") or "").strip()
        relative = str((provenance or {}).get("relative") or "").strip()
        if not run_dir or not relative:
            raise ArtifactActionError("this image has no file on disk to act on")
        raw = str(Path(run_dir) / relative)
    candidate = Path(raw).expanduser()
    real = _resolved_inside(candidate)
    if real is None:
        raise ArtifactActionError(
            "refused: the file is outside the run directory, so nothing was touched"
        )
    if not real.is_file():
        raise ArtifactActionError("the file is already gone from the run directory")
    return real


def _sidecar(path: Path, suffix: str, payload: Dict[str, Any]) -> Path:
    target = path.with_name(path.name + suffix)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return target


def keep(provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Mark one file as wanted. Reversible: the sidecar is the only change."""
    path = resolve(provenance)
    sidecar = _sidecar(
        path, KEEP_SUFFIX, {"kept_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "file": str(path)}
    )
    return {"action": "keep", "kept": True, "file": str(path), "marker": str(sidecar)}


def is_kept(provenance: Dict[str, Any]) -> bool:
    try:
        path = resolve(provenance)
    except ArtifactActionError:
        return False
    return path.with_name(path.name + KEEP_SUFFIX).exists()


def trash(provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Move one file into the trash, with a sidecar recording where it came from."""
    path = resolve(provenance)
    root = runs_root()
    folder = Path(os.path.realpath(root / TRASH_DIR / path.parent.name))
    real_root = Path(os.path.realpath(root))
    try:
        folder.relative_to(real_root)
    except ValueError as exc:  # pragma: no cover - guarded by resolve already
        raise ArtifactActionError("refused: the trash folder escapes the run directory") from exc
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    index = 1
    while target.exists():
        target = folder / f"{path.stem}-{index}{path.suffix}"
        index += 1
    shutil.move(str(path), str(target))
    digest = hashlib.sha1(target.read_bytes()).hexdigest()
    _sidecar(
        target,
        ".json",
        {
            "from": str(path),
            "trashed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "sha1": digest,
            "bytes": target.stat().st_size,
        },
    )
    return {"action": "trash", "trashed": True, "moved_to": str(target), "from": str(path)}


def _wanted(provenance: Dict[str, Any]) -> Path:
    """The path an artifact points at, whether or not it still exists.

    :func:`resolve` refuses a missing file because it is about to read or move it.
    The undo of a trash needs the opposite: the file is gone by construction, and the
    point is to put it back where it was.
    """
    raw = str((provenance or {}).get("path") or "").strip()
    if not raw:
        run_dir = str((provenance or {}).get("run_dir") or "").strip()
        relative = str((provenance or {}).get("relative") or "").strip()
        if not run_dir or not relative:
            raise ArtifactActionError("this image has no file on disk to act on")
        raw = str(Path(run_dir) / relative)
    real = _resolved_inside(Path(raw).expanduser())
    if real is None:
        raise ArtifactActionError(
            "refused: the file is outside the run directory, so nothing was touched"
        )
    return real


def restore(provenance: Dict[str, Any]) -> Dict[str, Any]:
    """Undo a ``trash``: bring the file back to the path it was taken from.

    The search is by provenance, not by name: a second image with the same name gets
    a ``-1`` suffix in the trash, and restoring by name would bring back the wrong
    file. The sidecar written by :func:`trash` records ``from`` - that is the key.
    """
    path = _wanted(provenance)
    folder = Path(os.path.realpath(runs_root() / TRASH_DIR))
    if not folder.is_dir():
        raise ArtifactActionError("rien à restaurer : la corbeille est vide")
    wanted = str(path)
    for sidecar in sorted(folder.rglob("*.json")):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(data.get("from") or "") != wanted:
            continue
        moved = sidecar.with_name(sidecar.name[: -len(".json")])
        if not moved.is_file():
            continue
        if path.exists():
            raise ArtifactActionError(
                "refusé : un fichier existe déjà à cet emplacement, restauration annulée"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(moved), str(path))
        sidecar.unlink(missing_ok=True)
        return {"action": "restore", "restored": True, "file": str(path)}
    raise ArtifactActionError("rien à restaurer : ce fichier n'est pas dans la corbeille")


def rate(
    provenance: Dict[str, Any],
    vote: str,
    *,
    session_id: str = "",
    message_id: Any = None,
    index: int = 0,
    comment: str = "",
) -> Dict[str, Any]:
    """Record one opinion: a local line first, delivery to the brain later.

    ``vote`` is ``up``, ``down`` or ``none``. ``none`` is the retraction of a vote
    (clicking the active thumb again): it is journalled like the others, because a
    history that silently loses a change of mind cannot be used to improve anything.
    """
    vote = str(vote or "").strip().lower()
    if vote not in VOTES and vote != VOTE_CLEAR:
        raise ArtifactActionError("a vote is 'up', 'down' or 'none'")
    try:
        path: Optional[Path] = resolve(provenance)
    except ArtifactActionError:
        path = None  # a vote on an image whose file is gone is still a signal
    line = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "vote": vote,
        "session": session_id,
        "message": message_id,
        "index": index,
        "file": str(path) if path else "",
        "comment": str(comment or "")[:500],
    }
    target = Path(RATINGS_FILE).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return {"action": "rate", "rated": vote, "journal": str(target)}


def trash_count() -> int:
    """How many files wait in the trash (the page shows it before emptying)."""
    folder = runs_root() / TRASH_DIR
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.rglob("*") if path.is_file())


def empty_trash() -> Dict[str, Any]:
    """Delete what the trash holds. Only the trash, never a run directory."""
    folder = runs_root() / TRASH_DIR
    if not folder.is_dir():
        return {"action": "empty_trash", "removed": 0}
    removed = sum(1 for path in folder.rglob("*") if path.is_file())
    shutil.rmtree(folder)
    return {"action": "empty_trash", "removed": removed}


__all__ = [
    "ArtifactActionError",
    "RUNS_ROOT",
    "RATINGS_FILE",
    "TRASH_DIR",
    "VOTES",
    "VOTE_CLEAR",
    "empty_trash",
    "is_kept",
    "keep",
    "rate",
    "resolve",
    "restore",
    "runs_root",
    "trash",
    "trash_count",
]
