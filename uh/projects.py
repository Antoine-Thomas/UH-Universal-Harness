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
    """Read the header line of ``status --porcelain=v1 -b`` without losing the raw text.

    ``dirty`` reste le total (le panneau dit « N fichier(s) »), mais les deux familles
    sont comptées à part : un fichier **non suivi** n'est pas une modification du dépôt.
    C'est cette distinction qui empêchait le pied de page de mentir sur un SVG oublié.
    """
    ahead = behind = 0
    branch = ""
    entries = 0
    tracked = 0
    untracked = 0
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
        if not line.strip():
            continue
        entries += 1
        if line.startswith("??"):
            untracked += 1
        else:
            tracked += 1
    return {
        "branch": branch,
        "dirty": entries,
        "tracked": tracked,
        "untracked": untracked,
        "ahead": ahead,
        "behind": behind,
    }


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
            "tracked": 0,
            "untracked": 0,
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
            "tracked": 0,
            "untracked": 0,
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
        "tracked": facts["tracked"],
        "untracked": facts["untracked"],
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


# --------------------------------------------------------------------------- #
# git, extended (P3-16c lot 2)
# --------------------------------------------------------------------------- #
#: Un journal qui demande 30 lignes en affiche 30 : on en demande une de plus pour
#: savoir s'il en reste, sans compter sur un total (git log n'en donne pas).
LOG_FIELDS = "%H%x1f%h%x1f%an%x1f%aI%x1f%s"
DIFF_MAX_CHARS = 200_000
COMMIT_MAX_CHARS = 5_000


def writable(path: str | Path) -> Path:
    """Le seul dépôt où une opération Git peut écrire : enregistré et sous la racine.

    ``repo_root`` valide que c'est un dépôt ; ``_permitted`` reprend la règle du registre
    et de ``projects.root``. Une opération qui écrit ne doit pas pouvoir viser un dépôt
    que l'opérateur n'a jamais déclaré.
    """
    root = repo_root(path)
    if root is None:
        raise ProjectsError("ce dossier n'est pas un dépôt git")
    if not _permitted(root):
        raise ProjectsError(f"{root} n'est ni enregistré ni sous projects.root")
    return root


def _checked_relpath(root: Path, raw: str) -> str:
    """Un chemin de fichier relatif au dépôt, ou un refus explicite.

    Les chemins absolus sont refusés plutôt que convertis : l'interface n'a aucune raison
    d'en envoyer, et accepter les deux formes rendrait la borne dépendante d'un cas.
    ``..`` est normalisé par ``resolve()`` avant la comparaison, donc ``a/../../b`` finit
    dehors et se fait refuser comme tel.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        raise ProjectsError("fichier requis")
    candidate = Path(text)
    if candidate.is_absolute() or text.startswith("/"):
        raise ProjectsError(f"chemin absolu refusé : {text}")
    base = root.resolve()
    try:
        resolved = (base / candidate).resolve()
        relative = resolved.relative_to(base)
    except (OSError, ValueError) as exc:
        raise ProjectsError(f"hors du dépôt : {text}") from exc
    return relative.as_posix()


def _repo(path: str | Path) -> Path:
    root = repo_root(path)
    if root is None:
        raise ProjectsError("ce dossier n'est pas un dépôt git")
    return root


def log(path: str | Path, limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    """Le journal, paginé. Lecture seule : un dépôt non écrit est lisible."""
    root = _repo(path)
    wanted = max(1, min(int(limit or 30), 200))
    skip = max(0, int(offset or 0))
    try:
        proc = _git(root, "log", f"--max-count={wanted + 1}", f"--skip={skip}", f"--pretty={LOG_FIELDS}")
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectsError(f"git log a échoué : {type(exc).__name__}") from exc
    if proc.returncode != 0:
        # Un dépôt sans commit : ce n'est pas une erreur, c'est un journal vide.
        reason = (proc.stderr or "").strip()
        return {"ok": True, "commits": [], "limit": wanted, "offset": skip,
                "has_more": False, "reason": reason[:200]}
    commits: List[Dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 5:
            continue
        commits.append({"sha": parts[0], "short": parts[1], "author": parts[2],
                        "date": parts[3], "subject": parts[4]})
    return {
        "ok": True,
        "commits": commits[:wanted],
        "limit": wanted,
        "offset": skip,
        "has_more": len(commits) > wanted,
        "path": str(root),
    }


def diff(path: str | Path, file: str = "", staged: bool = False) -> Dict[str, Any]:
    """Un diff unifié lisible : espaces de fin ignorés, ce que ``--ignore-space-at-eol`` fait."""
    root = _repo(path)
    relative = _checked_relpath(root, file) if str(file or "").strip() else ""
    args = ["diff", "--no-color", "--ignore-space-at-eol"]
    if staged:
        args.append("--cached")
    if relative:
        args += ["--", relative]
    try:
        proc = _git(root, *args)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectsError(f"git diff a échoué : {type(exc).__name__}") from exc
    body = proc.stdout or ""
    return {
        "ok": proc.returncode == 0,
        "file": relative,
        "staged": bool(staged),
        "diff": body[:DIFF_MAX_CHARS],
        "bytes": len(body),
        "truncated": len(body) > DIFF_MAX_CHARS,
        "reason": "" if proc.returncode == 0 else (proc.stderr or "").strip()[:200],
    }


def _index(root: Path, files: Iterable[str], *, add: bool) -> List[str]:
    rels: List[str] = []
    for raw in files or []:
        rel = _checked_relpath(root, raw)
        if rel not in rels:
            rels.append(rel)
    if not rels:
        raise ProjectsError("aucun fichier")
    if add:
        proc = _git(root, "add", "--", *rels)
    else:
        proc = _git(root, "reset", "-q", "HEAD", "--", *rels)
        if proc.returncode != 0:
            # Dépôt sans commit : pas de HEAD à comparer, on retire de l'index autrement.
            proc = _git(root, "rm", "--cached", "-r", "-q", "--", *rels)
    if proc.returncode != 0:
        raise ProjectsError((proc.stderr or proc.stdout or "git a refusé").strip()[:400])
    return rels


def stage(path: str | Path, files: Iterable[str]) -> Dict[str, Any]:
    root = writable(path)
    rels = _index(root, files, add=True)
    return {"ok": True, "staged": rels, "project": describe(root)}


def unstage(path: str | Path, files: Iterable[str]) -> Dict[str, Any]:
    root = writable(path)
    rels = _index(root, files, add=False)
    return {"ok": True, "unstaged": rels, "project": describe(root)}


def commit(path: str | Path, message: str) -> Dict[str, Any]:
    """Un commit, avec un message obligatoire. Jamais ``--no-verify``, jamais de push.

    Les hooks du dépôt tournent : un hook qui refuse est une réponse légitime, et elle
    est rapportée telle quelle. Rien ici ne pousse, rien ne force.
    """
    root = writable(path)
    text = str(message or "").strip()
    if not text:
        raise ProjectsError("un message de commit est obligatoire")
    if len(text) > COMMIT_MAX_CHARS:
        raise ProjectsError(f"message trop long ({len(text)} caractères, maximum {COMMIT_MAX_CHARS})")
    try:
        proc = _git(root, "commit", "-m", text)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProjectsError(f"git commit a échoué : {type(exc).__name__}") from exc
    if proc.returncode != 0:
        raise ProjectsError((proc.stderr or proc.stdout or "git commit a refusé").strip()[:600])
    sha = _git(root, "rev-parse", "HEAD").stdout.strip()
    return {
        "ok": True,
        "sha": sha,
        "short": sha[:7],
        "message": text,
        "project": describe(root),
    }



__all__ = [
    "GIT_TIMEOUT",
    "REGISTRY",
    "ProjectsError",
    "add",
    "allowed_root",
    "commit",
    "describe",
    "diff",
    "listing",
    "load_registry",
    "log",
    "parse_porcelain",
    "registry_path",
    "remove",
    "repo_root",
    "save_registry",
    "stage",
    "switch",
    "unstage",
    "writable",
]
