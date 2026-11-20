"""Projects and their Git state, as the cockpit needs to show them.

Two rules, both learned the hard way in this repository:

* **Never invent a status.** ``describe()`` returns the *raw* ``git status --porcelain``
  string beside its own reading of it. The panel shows the summary, the proof compares the
  raw string to what a terminal prints - byte for byte, which is what Gate D asks for.
* **Never let a broken repository break the panel.** A directory that is not a repository,
  a git that refuses, a missing path: each comes back as ``ok: False`` with a readable
  ``reason``. A cockpit that raises on one bad project cannot be used to fix it.

The registry lives in ``~/.uh/projects.json`` (written by the interface, one entry per
project) and ``projects.root`` in ``~/.uh/config.toml`` is the optional fence: a path
outside both the registry and that root is refused before anything is opened.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REGISTRY = "projects.json"
#: Seconds a git call may take before we call it dead: a hung mount must not hang a panel.
GIT_TIMEOUT = 8.0


class ProjectsError(RuntimeError):
    """A refusal the interface can show: nothing was changed."""


def home() -> Path:
    return Path(os.environ.get("UH_HOME") or (Path.home() / ".uh"))


def registry_path() -> Path:
    return home() / REGISTRY


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(path), *args),
        capture_output=True,
        text=True,
        # git rend de l'UTF-8 ; sans ceci, `text=True` decode avec cp1252 sur un Windows non
        # UTF-8 et un nom de fichier accentue revient corrompu dans le panneau Projets.
        encoding="utf-8",
        timeout=GIT_TIMEOUT,
        check=False,
        # A repository owned by someone else must not make git refuse to look, and the
        # panel must not run the operator's hooks: reading is what happens here.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def repo_root(path: str | Path) -> Optional[Path]:
    """The repository containing ``path``, or ``None`` - the only validation that counts."""
    target = Path(path).expanduser()
    if not target.exists():
        return None
    if not target.is_dir():
        target = target.parent
    try:
        proc = _git(target, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    root = proc.stdout.strip()
    return Path(root) if root else None


def parse_porcelain(raw: str) -> Dict[str, Any]:
    """Read the header line of ``status --porcelain=v1 -b`` without losing the raw text."""
    ahead = behind = 0
    branch = ""
    entries = 0
    for line in raw.splitlines():
        if line.startswith("## "):
            header = line[3:]
            # `main...origin/main [ahead 2, behind 1]`
            name = header.split("...")[0].strip()
            branch = name or branch
            if "[" in header and "]" in header:
                inside = header[header.index("[") + 1 : header.rindex("]")]
                for part in inside.split(","):
                    part = part.strip()
                    if part.startswith("ahead "):
                        ahead = int(part.split()[1])
                    elif part.startswith("behind "):
                        behind = int(part.split()[1])
            continue
        if line.strip():
            entries += 1
    return {"branch": branch, "dirty": entries, "ahead": ahead, "behind": behind}


def describe(path: str | Path) -> Dict[str, Any]:
    """Everything the panel says about one project, including why it says nothing."""
    folder = Path(path).expanduser()
    root = repo_root(folder)
    if root is None:
        return {
            "path": str(folder),
            "ok": False,
            "reason": "ce dossier n'est pas un dépôt git",
            "branch": "",
            "porcelain": "",
            "dirty": 0,
            "ahead": 0,
            "behind": 0,
            "last_commit": "",
            "name": folder.name,
        }
    try:
        status = _git(root, "status", "--porcelain=v1", "-b")
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "path": str(root),
            "ok": False,
            "reason": f"git status a échoué : {type(exc).__name__}",
            "porcelain": "",
            "branch": "",
            "dirty": 0,
            "ahead": 0,
            "behind": 0,
            "last_commit": "",
            "name": root.name,
        }
    raw = status.stdout
    facts = parse_porcelain(raw)
    log = _git(root, "log", "-1", "--pretty=%h%x09%ad%x09%s", "--date=short")
    return {
        "path": str(root),
        "name": root.name,
        "ok": status.returncode == 0,
        "reason": "" if status.returncode == 0 else (status.stderr or "").strip()[:200],
        # The raw text is the evidence: D-4 compares it to a terminal's output.
        "porcelain": raw,
        "branch": facts["branch"],
        "dirty": facts["dirty"],
        "ahead": facts["ahead"],
        "behind": facts["behind"],
        "last_commit": log.stdout.strip() if log.returncode == 0 else "",
    }


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
def load_registry() -> List[Dict[str, Any]]:
    target = registry_path()
    if not target.is_file():
        return []
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict):
        rows = data.get("projects")
    else:
        rows = data
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("path")]


def save_registry(rows: Iterable[Dict[str, Any]]) -> None:
    target = registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"projects": list(rows)}
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def allowed_root() -> Optional[Path]:
    """``projects.root`` from the config, when the operator set one."""
    from . import config as uh_config

    try:
        loaded = uh_config.load()
    except Exception:  # noqa: BLE001 - a broken config must not break the panel
        return None
    value = getattr(loaded, "projects_root", None)
    if not value:
        ignored = getattr(loaded, "extra", None) or {}
        value = (ignored.get("projects") or {}).get("root") if isinstance(ignored, dict) else None
    if not value:
        return None
    return Path(str(value)).expanduser()


def _permitted(root: Path) -> bool:
    """A project is allowed if it is registered, or inside the configured root."""
    real = Path(os.path.realpath(root))
    for row in load_registry():
        if Path(os.path.realpath(str(row["path"]))) == real:
            return True
    fence = allowed_root()
    if fence is None:
        return True
    try:
        real.relative_to(Path(os.path.realpath(fence)))
    except ValueError:
        return False
    return True


def add(path: str | Path, label: str = "") -> Dict[str, Any]:
    root = repo_root(path)
    if root is None:
        raise ProjectsError("ce dossier n'est pas un dépôt git")
    if not _permitted(root):
        raise ProjectsError(f"{root} n'est ni enregistré ni sous projects.root")
    rows = [row for row in load_registry() if Path(str(row["path"])) != root]
    entry = {
        "path": str(root),
        "label": (label or root.name).strip()[:80],
        "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    rows.insert(0, entry)
    save_registry(rows[:200])
    return entry


def switch(path: str | Path) -> Path:
    """Where a session may move to. Refused unless it is a repository we already know."""
    root = repo_root(path)
    if root is None:
        raise ProjectsError("ce dossier n'est pas un dépôt git")
    if not _permitted(root):
        raise ProjectsError(
            f"{root} n'est ni enregistré ni sous projects.root : ajoutez-le d'abord"
        )
    return root


def remove(path: str | Path) -> bool:
    root = repo_root(path) or Path(path).expanduser()
    rows = load_registry()
    kept = [row for row in rows if Path(str(row["path"])) != root]
    if len(kept) == len(rows):
        return False
    save_registry(kept)
    return True


def listing(current: str | Path | None = None) -> Dict[str, Any]:
    """The current project first, then the registry, each with its measured state."""
    seen: Dict[str, Dict[str, Any]] = {}
    if current:
        described = describe(current)
        described["current"] = True
        seen[described["path"]] = described
    for row in load_registry():
        described = describe(str(row["path"]))
        described["label"] = str(row.get("label") or described.get("name") or "")
        if described["path"] not in seen:
            described["current"] = False
            seen[described["path"]] = described
    projects = list(seen.values())
    return {
        "current": str(Path(current).expanduser()) if current else "",
        "projects": projects,
        "root": str(allowed_root() or ""),
    }


__all__ = [
    "GIT_TIMEOUT",
    "REGISTRY",
    "ProjectsError",
    "add",
    "allowed_root",
    "describe",
    "listing",
    "load_registry",
    "parse_porcelain",
    "registry_path",
    "remove",
    "repo_root",
    "save_registry",
    "switch",
]
