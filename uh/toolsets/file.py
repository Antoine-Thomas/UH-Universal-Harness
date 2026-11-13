"""File toolset: read, write, targeted patch, regex search - sandboxed.

Sandbox contract
----------------
Every path goes through ``_resolve``:

1. relative paths are joined to the workspace;
2. paths that *look* absolute (``/x``, ``\\x``, ``C:x``) are made absolute with
   ``os.path.abspath`` - on Windows ``"/etc/passwd"`` has no drive and would
   otherwise silently become ``<workspace>/etc/passwd``;
3. ``Path.resolve()`` follows symlinks and junctions;
4. the result must satisfy ``is_relative_to(workspace.resolve())``.

Anything outside is refused with ``{"error": ...}`` - no exception, no write,
including when the escape happens through a symlink pointing outside.

Workspace = ``UH_WORKSPACE`` if set, else the current directory - unless that
directory is system-owned (``C:\\Windows``, ``/usr``, ...), in which case
``~/uh-workspace`` is used and the console says so (see :mod:`uh.sandbox`).
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable, List

from ..sandbox import (
    allowed_paths,
    default_workspace,
    is_allowed,
    outside_message,
)

MAX_READ_BYTES = 1_000_000
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_FILE_BYTES = 1_000_000
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build", ".pytest_cache"}


TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Workspace-relative path"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write (create or overwrite) a UTF-8 text file inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": (
                "Replace an anchor (old_string) by new_string in one file. "
                "The anchor must be unique unless replace_all is true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search a regular expression in workspace files. Returns path, line and text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regular expression"},
                    "path": {"type": "string", "description": "Directory to search, default '.'"},
                    "file_glob": {"type": "string", "description": "e.g. '*.py'"},
                },
                "required": ["pattern"],
            },
        },
    },
]

ALLOWED_ARGS = {
    "read_file": {"path"},
    "write_file": {"path", "content"},
    "patch": {"path", "old_string", "new_string", "replace_all"},
    "search_files": {"pattern", "path", "file_glob"},
}

REQUIRED_ARGS = {
    "read_file": {"path"},
    "write_file": {"path", "content"},
    "patch": {"path", "old_string", "new_string"},
    "search_files": {"pattern"},
}


class FileToolset:
    name = "file"
    tools = TOOLS

    def __init__(
        self,
        workspace: str | os.PathLike[str] | None = None,
        allowed: list[Path] | None = None,
    ) -> None:
        self.workspace = (
            Path(workspace).expanduser().resolve() if workspace else default_workspace()
        )
        # Writes may leave the workspace for a whitelisted directory; reads may
        # not, which keeps the model from wandering the disk.
        self.allowed = list(allowed) if allowed is not None else allowed_paths(self.workspace)

    def set_workspace(self, workspace: str | os.PathLike[str]) -> None:
        """Move the sandbox: the old workspace stops being writable."""
        self.workspace = Path(workspace).expanduser().resolve()
        self.allowed = allowed_paths(self.workspace)

    # -- plumbing ---------------------------------------------------------- #
    def call(self, name: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        handlers: dict[str, Callable[..., dict]] = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "patch": self.patch,
            "search_files": self.search_files,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"unknown file tool '{name}'. Known: {sorted(ALLOWED_ARGS)}"}

        unexpected = set(args) - ALLOWED_ARGS[name]
        if unexpected:
            return {"error": f"unexpected argument(s) for {name}: {sorted(unexpected)}"}
        missing = REQUIRED_ARGS[name] - set(args)
        if missing:
            return {"error": f"missing argument(s) for {name}: {sorted(missing)}"}

        try:
            return handler(**args)
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def _resolve(self, raw: Any, *, write: bool = False) -> tuple[Path | None, dict | None]:
        text = str(raw or "").strip()
        if not text:
            return None, {"error": "path is required"}

        candidate = Path(text).expanduser()
        looks_absolute = (
            candidate.is_absolute()
            or text.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", text) is not None
        )
        if looks_absolute:
            candidate = Path(os.path.abspath(os.path.expanduser(text)))
        else:
            candidate = self.workspace / candidate

        try:
            resolved = candidate.resolve()
        except OSError as exc:
            return None, {"error": f"cannot resolve path '{text}': {exc}"}

        if resolved == self.workspace or resolved.is_relative_to(self.workspace):
            return resolved, None
        if not write:
            # Reading stays workspace-only: a write leaves files behind, a read
            # would let the model wander the disk for anything interesting.
            return None, {
                "error": (
                    f"refused: '{text}' resolves to {resolved}, "
                    f"outside the workspace {self.workspace}"
                )
            }
        if not is_allowed(resolved, self.workspace, self.allowed):
            return None, {"error": outside_message(text, self.workspace, self.allowed)}
        return resolved, None

    def _relative(self, resolved: Path) -> str:
        try:
            return resolved.relative_to(self.workspace).as_posix()
        except ValueError:  # pragma: no cover - guarded by _resolve
            return str(resolved)

    # -- tools ------------------------------------------------------------- #
    def read_file(self, path: str) -> dict:
        target, error = self._resolve(path)
        if error:
            return error
        assert target is not None
        if not target.exists():
            return {"error": f"not found: {self._relative(target)}"}
        if target.is_dir():
            return {"error": f"is a directory: {self._relative(target)} (use search_files)"}
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            return {"error": f"file too large ({size} bytes > {MAX_READ_BYTES})"}
        content = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": self._relative(target),
            "content": content,
            "bytes": size,
            "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
        }

    def write_file(self, path: str, content: str) -> dict:
        target, error = self._resolve(path, write=True)
        if error:
            return error
        assert target is not None
        if target.is_dir():
            return {"error": f"is a directory: {self._relative(target)}"}
        created = not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
        return {
            "path": self._relative(target),
            "bytes": target.stat().st_size,
            "created": created,
        }

    def patch(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> dict:
        target, error = self._resolve(path, write=True)
        if error:
            return error
        assert target is not None
        if not target.exists():
            return {"error": f"not found: {self._relative(target)}"}
        if not old_string:
            return {"error": "old_string must not be empty"}

        original = target.read_text(encoding="utf-8")
        occurrences = original.count(old_string)
        if occurrences == 0:
            return {"error": f"anchor not found in {self._relative(target)}"}
        if occurrences > 1 and not replace_all:
            return {
                "error": (
                    f"anchor is ambiguous in {self._relative(target)} "
                    f"({occurrences} matches); set replace_all=true or extend the anchor"
                )
            }

        updated = original.replace(old_string, new_string, -1 if replace_all else 1)
        target.write_text(updated, encoding="utf-8")
        return {
            "path": self._relative(target),
            "replacements": occurrences if replace_all else 1,
            "bytes": target.stat().st_size,
        }

    def search_files(
        self,
        pattern: str,
        path: str = ".",
        file_glob: str | None = None,
    ) -> dict:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return {"error": f"invalid regular expression '{pattern}': {exc}"}

        root, error = self._resolve(path or ".")
        if error:
            return error
        assert root is not None
        if root.is_file():
            files: Iterable[Path] = [root]
        else:
            files = self._walk(root)

        matches: List[dict] = []
        truncated = False
        for candidate in files:
            if file_glob and not fnmatch.fnmatch(candidate.name, file_glob):
                continue
            try:
                if candidate.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
                text = candidate.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        {
                            "path": self._relative(candidate),
                            "line": number,
                            "text": line[:400],
                        }
                    )
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        truncated = True
                        break
            if truncated:
                break

        return {"matches": matches, "count": len(matches), "truncated": truncated}

    def _walk(self, root: Path) -> Iterable[Path]:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in filenames:
                yield Path(dirpath) / filename
