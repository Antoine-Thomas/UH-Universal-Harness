"""Where UH may work: the workspace rule, and the paths it may write to.

Two questions live here, both answered without dependencies:

* **which directory a session starts in** - a console opened from
  ``C:\\Windows\\System32`` must not adopt the OS as its workspace, so the
  current directory is used only when it is an ordinary one;
* **which paths the file and terminal toolsets accept**, given that workspace
  (the whitelist lives here too, so the file and terminal toolsets cannot
  disagree about it).

Pure functions except :func:`fallback_workspace`, which creates
``~/uh-workspace`` on demand.
"""

from __future__ import annotations

import os
from pathlib import Path

# Directories where an agent must never treat the current directory as its
# workspace: a shell started there is looking at the operating system, not at a
# project. Compared textually (see is_system_directory), so a path from another
# platform is answered correctly too.
SYSTEM_DIRECTORIES: tuple[str, ...] = (
    "C:/Windows",
    "C:/Program Files",
    "C:/Program Files (x86)",
    "C:/ProgramData",
    "/usr",
    "/etc",
    "/bin",
    "/sbin",
    "/System",
    "/Library",
)

FALLBACK_DIRNAME = "uh-workspace"

SYSTEM_WORKSPACE_WARNING = (
    "[WARN] cwd is a system directory; using ~/uh-workspace instead. "
    "Use /workspace <path> to switch."
)


def _normalised(path: str | os.PathLike[str]) -> str:
    """Forward slashes, no trailing separator, lower case.

    Deliberately not ``abspath``: on Windows ``/usr`` becomes ``C:\\usr`` and the
    comparison would answer for the current drive, and on Linux ``C:/Windows``
    would become a path under the cwd. Comparing the *shapes* keeps the rule
    identical on every platform.
    """
    return str(path).replace("\\", "/").rstrip("/").lower()


def is_system_directory(path: str | os.PathLike[str] | None) -> bool:
    """True when ``path`` *is* one of :data:`SYSTEM_DIRECTORIES`, or sits under it.

    Textual on purpose: the answer must be right for a directory that does not
    exist on this platform (``C:/Windows`` on Linux), and the caller has already
    resolved its own candidate, so no second ``resolve()`` is applied here.
    """
    if not path:
        return False
    candidate = _normalised(path)
    for entry in SYSTEM_DIRECTORIES:
        root = _normalised(entry)
        if candidate == root or candidate.startswith(root + "/"):
            return True
    return False


def fallback_workspace(home: Path | None = None) -> Path:
    """``~/uh-workspace``, created when missing. This is the safe default."""
    root = (Path(home) if home is not None else Path.home()) / FALLBACK_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def default_workspace() -> Path:
    """``UH_WORKSPACE`` verbatim, else the cwd - unless the cwd is system-owned.

    An explicit ``UH_WORKSPACE`` is trusted as given: the user said where they
    want to work. Only the implicit choice is guarded.
    """
    raw = os.environ.get("UH_WORKSPACE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    cwd = Path.cwd().resolve()
    if is_system_directory(cwd):
        return fallback_workspace()
    return cwd


__all__ = [
    "SYSTEM_DIRECTORIES",
    "SYSTEM_WORKSPACE_WARNING",
    "FALLBACK_DIRNAME",
    "is_system_directory",
    "fallback_workspace",
    "default_workspace",
]
