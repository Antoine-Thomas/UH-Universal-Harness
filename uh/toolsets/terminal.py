"""Terminal toolset: one non-interactive command, hard timeout, blacklist.

No TTY, no interactive shell, no persistent session: each call is a fresh
``subprocess.run(..., shell=True)`` started in the workspace.

Blacklist (documented rule, checked BEFORE any subprocess is spawned)
--------------------------------------------------------------------
A command is refused when it matches one of these classes, case-insensitively:

1. recursive delete of a filesystem root, the home directory or everything
   (``rm -rf /``, ``rm -rf ~``, ``rm -rf *``, ``rm -r -f /home``),
2. filesystem creation/formatting (``mkfs.ext4 /dev/sda1``, ``format C:``),
3. raw disk writes (``dd ... of=/dev/sda``),
4. Windows mass deletion (``del /f /s /q C:\\``, ``rd /s /q C:\\``),
5. machine shutdown/reboot (``shutdown``, ``reboot``, ``halt``, ``poweroff``),
6. fork bombs (``:(){ :|:& };:``),
7. world-writable recursive chmod on a root path (``chmod -R 777 /``),
8. piping a download straight into a shell (``curl ... | sh``),
9. history-rewriting pushes (``git push --force``, ``git push -f``).

The list is deliberately finite and auditable: it blocks obvious catastrophes,
it is not a security sandbox. Anything else runs and the sandbox stays the
responsibility of the operator.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List

from ..sandbox import allowed_paths
from .file import default_workspace

MAX_OUTPUT_CHARS = 20_000
MAX_TIMEOUT = 600

TOOLS: List[dict] = [
    {
        "type": "function",
        "function": {
            "name": "terminal",
            "description": (
                "Run one non-interactive shell command in the workspace. "
                "Returns exit_code, stdout, stderr. Destructive commands are refused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Seconds, default 60, max 600"},
                },
                "required": ["command"],
            },
        },
    },
]

ALLOWED_ARGS = {"terminal": {"command", "timeout"}}

BLACKLIST: tuple[tuple[str, str], ...] = (
    (
        r"\brm\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf][a-zA-Z]*(?:\s+-[a-zA-Z]+)*\s+(?:/|~|\$HOME|\*|/\*)(?:\s|$)",
        "recursive delete of a filesystem root, home or everything",
    ),
    (r"\bmkfs(?:\.\w+)?\b", "filesystem formatting"),
    (r"\bformat\s+[a-zA-Z]:", "Windows drive formatting"),
    (r"\bdd\b[^\n]*\bof=/dev/", "raw disk write"),
    (r"\bdel\s+(?:/[a-zA-Z]\s+)*/[fsq][a-zA-Z]*(?:\s+/[a-zA-Z]+)*\s+[a-zA-Z]:", "Windows mass deletion"),
    (r"\brd\s+/s\s+(?:/q\s+)?[a-zA-Z]:", "Windows recursive directory removal of a drive"),
    (r"\b(?:shutdown|reboot|halt|poweroff)\b", "machine shutdown or reboot"),
    (r":\(\)\s*\{.*:\|:&.*\}\s*;\s*:", "fork bomb"),
    (r"\bchmod\s+-R\s+0?777\s+/(?:\s|$)", "world-writable recursive chmod on root"),
    (r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b", "download piped into a shell"),
    (r"\bgit\s+push\b[^\n]*(?:--force|-f)(?:\s|$)", "history-rewriting force push"),
)

_COMPILED = tuple((re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in BLACKLIST)


def check_command(command: str) -> dict | None:
    """Return an error payload if the command is blacklisted, else ``None``."""
    stripped = command.strip()
    if not stripped:
        return {"error": "command is required"}
    for regex, reason in _COMPILED:
        if regex.search(stripped):
            return {
                "error": (
                    f"refused: command matches the destructive blacklist ({reason}); "
                    "nothing was executed"
                ),
                "refused": True,
            }
    return None


class TerminalToolset:
    name = "terminal"
    tools = TOOLS

    def __init__(
        self,
        workspace: str | os.PathLike[str] | None = None,
        allowed: list[Path] | None = None,
    ) -> None:
        self.workspace = (
            Path(workspace).expanduser().resolve() if workspace else default_workspace()
        )
        self.allowed = list(allowed) if allowed is not None else allowed_paths(self.workspace)

    def set_workspace(self, workspace: str | os.PathLike[str]) -> None:
        """Move the shell's cwd and the write whitelist with it."""
        self.workspace = Path(workspace).expanduser().resolve()
        self.allowed = allowed_paths(self.workspace)

    def call(self, name: str, args: dict | None = None) -> dict:
        args = dict(args or {})
        if name != "terminal":
            return {"error": f"unknown terminal tool '{name}'. Known: ['terminal']"}
        unexpected = set(args) - ALLOWED_ARGS["terminal"]
        if unexpected:
            return {"error": f"unexpected argument(s) for terminal: {sorted(unexpected)}"}
        if "command" not in args:
            return {"error": "missing argument(s) for terminal: ['command']"}
        return self.terminal(args["command"], args.get("timeout", 60))

    def terminal(self, command: str, timeout: int = 60) -> dict:
        refusal = check_command(command)
        if refusal:
            return refusal

        seconds = max(1, min(int(timeout or 60), MAX_TIMEOUT))
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=seconds,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"timeout after {seconds}s: command killed", "timeout": True}
        except OSError as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

        stdout, out_cut = _truncate(completed.stdout or "")
        stderr, err_cut = _truncate(completed.stderr or "")
        return {
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": out_cut or err_cut,
        }


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    return text[:MAX_OUTPUT_CHARS] + f"\n[... truncated {len(text) - MAX_OUTPUT_CHARS} chars]", True
