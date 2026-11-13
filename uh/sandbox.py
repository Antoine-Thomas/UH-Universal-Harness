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

# Directories a session may write to even when they are not the workspace.
# ~/Desktop for a file the user asked to see on their desktop, ~/Documents for a
# document, ~/Downloads for something fetched, ~/uh-workspace for scratch files.
ALLOWED_HOME_DIRS: tuple[str, ...] = ("Desktop", "Documents", "Downloads", FALLBACK_DIRNAME)
ENV_ALLOWED_PATHS = "UH_ALLOWED_PATHS"

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


# --------------------------------------------------------------------------- #
# write whitelist
# --------------------------------------------------------------------------- #
def home_directories(home: Path | None = None) -> list[Path]:
    """The four directories a session may write to without changing workspace."""
    root = Path(home) if home is not None else Path.home()
    return [root / name for name in ALLOWED_HOME_DIRS]


def configured_paths() -> list[Path]:
    """Extra allowed paths: ``UH_ALLOWED_PATHS``, then ``[providers.file]``.

    The environment variable is the supported way to make the list persistent:
    UH's config writer is flat (``config.toml`` holds scalars only), so a nested
    table can be *read* but never written by ``uh config set``. A hand-written
    ``[providers.file] allowed_paths = [...]`` is honoured on read - that is the
    documented escape hatch, not a limitation to work around.
    """
    paths: list[Path] = []
    raw = os.environ.get(ENV_ALLOWED_PATHS, "")
    for item in raw.split(os.pathsep):
        item = item.strip()
        if item:
            paths.append(Path(item).expanduser())
    try:
        from . import config as uh_config

        paths.extend(uh_config.load().file_allowed_paths())
    except Exception:  # noqa: BLE001 - a broken config must not lock the toolsets
        pass
    return paths


def allowed_paths(
    workspace: str | os.PathLike[str] | None = None, home: Path | None = None
) -> list[Path]:
    """Everything a write may target, resolved and de-duplicated.

    The workspace comes first: it is what the refusal messages and ``/workspace``
    talk about, and it is always the directory the terminal runs in.
    """
    candidates: list[Path] = []
    if workspace is not None:
        candidates.append(Path(workspace))
    candidates.extend(home_directories(home))
    candidates.extend(configured_paths())

    resolved: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            path = Path(item).expanduser().resolve()
        except OSError:  # pragma: no cover - exotic paths only
            continue
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            resolved.append(path)
    return resolved


def display_paths(paths: list[Path]) -> str:
    """``~/Desktop, ~/Documents, ...`` - shortened for a refusal message."""
    home = str(Path.home())
    rendered: list[str] = []
    for path in paths:
        text = str(path)
        if text == home:
            text = "~"
        elif text.startswith(home + os.sep):
            # forward slashes on purpose: the message is read, not executed
            text = "~/" + text[len(home) + 1 :].replace("\\", "/")
        else:
            text = text.replace("\\", "/")
        rendered.append(text)
    return ", ".join(dict.fromkeys(rendered))


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def is_allowed(
    path: str | os.PathLike[str],
    workspace: str | os.PathLike[str] | None = None,
    allowed: list[Path] | None = None,
) -> bool:
    """True when ``path`` may be written to: inside the workspace or the whitelist.

    A system directory is refused whatever the whitelist says: adding ``/etc``
    to ``UH_ALLOWED_PATHS`` must not turn the sandbox into a root shell.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:  # pragma: no cover - exotic paths only
        return False
    if is_system_directory(resolved):
        return False
    if workspace is not None:
        try:
            if _under(resolved, Path(workspace).expanduser().resolve()):
                return True
        except OSError:  # pragma: no cover
            pass
    roots = allowed if allowed is not None else allowed_paths(workspace)
    return any(_under(resolved, root) for root in roots)


def outside_message(
    raw: str, workspace: str | os.PathLike[str] | None = None, allowed: list[Path] | None = None
) -> str:
    """The refusal text: why, where it may write, how to change that, and how to start over.

    Every refusal carries the list, because "refused" alone leaves the user with
    no next step - and the model with a temptation to try the shell instead.
    """
    roots = allowed if allowed is not None else allowed_paths(workspace)
    if is_system_directory(raw):
        head = f"refused: '{raw}' is a system path, which UH never writes to"
    else:
        head = (
            f"refused: '{raw}' is outside the workspace and outside the allowed paths "
            f"({display_paths(roots)})"
        )
    return (
        f"{head}. Use /workspace <path> to switch. "
        "(hint: /workspace <path> or `uh --workspace <path>`)"
    )


__all__ = [
    "SYSTEM_DIRECTORIES",
    "SYSTEM_WORKSPACE_WARNING",
    "FALLBACK_DIRNAME",
    "ALLOWED_HOME_DIRS",
    "ENV_ALLOWED_PATHS",
    "is_system_directory",
    "fallback_workspace",
    "default_workspace",
    "home_directories",
    "configured_paths",
    "allowed_paths",
    "display_paths",
    "is_allowed",
    "outside_message",
]
