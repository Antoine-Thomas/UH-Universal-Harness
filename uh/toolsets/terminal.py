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

Write destinations (added with the whitelist)
---------------------------------------------
Separately from the blacklist, the obvious write shapes are checked against the
same rule as the file toolset: the destination must be inside the workspace or
inside an allowed path (``~/Desktop``, ``~/Documents``, ``~/Downloads``,
``~/uh-workspace``, ``UH_ALLOWED_PATHS``), and never a system directory.

Refused shapes: ``> target``, ``>> target``, ``mkdir``, ``md``, ``touch``,
``tee``, ``cp``/``copy``/``mv``/``move``/``install``/``ln`` destinations.

This is a guard on *obvious* writes, not a sandbox. ``python -c
"open('/etc/passwd','w')"``, a script the model writes into the workspace and
then runs, ``cmd /c``, an editor, or any program that writes on its own still
goes through - by design: the shell executes programs, and pretending otherwise
would be worse than saying so. The file toolset is the enforced path; the
terminal is a seatbelt.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import List

from .. import sandbox
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

# Commands whose arguments are write destinations: "all" = every non-flag
# argument, "last" = the final one (cp/mv/install/ln put the destination last).
WRITE_COMMANDS: dict[str, str] = {
    "mkdir": "all",
    "md": "all",
    "touch": "all",
    "tee": "all",
    "cp": "last",
    "copy": "last",
    "mv": "last",
    "move": "last",
    "install": "last",
    "ln": "last",
}

# Redirection targets that write nothing on disk.
NULL_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "nul", "nul:"}


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


def _scan_command(command: str) -> tuple[list[list[str]], list[str]]:
    """Split a shell line into token segments plus redirection targets.

    Quote-aware and backslash-preserving: ``echo "a > b"`` is one token with no
    redirect, and ``C:\\Users\\x`` keeps its backslashes. ``;``, ``&&``, ``||``
    and ``|`` start a new segment, so only the command that owns a destination
    is judged.
    """
    segments: list[list[str]] = [[]]
    targets: list[str] = []
    token: list[str] = []
    quote: str | None = None
    index = 0

    def flush() -> None:
        nonlocal token
        if token:
            segments[-1].append("".join(token))
            token = []

    while index < len(command):
        char = command[index]
        if quote:
            if char == quote:
                quote = None
            else:
                token.append(char)
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if char == ">":
            flush()
            if segments[-1] and segments[-1][-1].isdigit() and len(segments[-1][-1]) <= 2:
                segments[-1].pop()  # "2>" names a file descriptor, not an argument
            index += 1
            if index < len(command) and command[index] == ">":
                index += 1
            while index < len(command) and command[index].isspace():
                index += 1
            chunk: list[str] = []
            inner: str | None = None
            while index < len(command):
                current = command[index]
                if inner:
                    if current == inner:
                        inner = None
                    else:
                        chunk.append(current)
                elif current in "\"'":
                    inner = current
                elif current.isspace() or current in ";&|<>":
                    break
                else:
                    chunk.append(current)
                index += 1
            if chunk:
                targets.append("".join(chunk))
            continue
        if char in ";&|":
            flush()
            segments.append([])
            index += 1
            continue
        if char.isspace():
            flush()
            index += 1
            continue
        token.append(char)
        index += 1

    flush()
    return [segment for segment in segments if segment], targets


def _destinations(segment: list[str]) -> list[str]:
    """Write destinations declared by one command segment (empty for the rest)."""
    words = list(segment)
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words.pop(0)  # FOO=bar mkdir ...: the assignment is not the command
    while words and words[0].lower() in {"sudo", "env", "command"}:
        words.pop(0)
    if not words:
        return []
    program = os.path.basename(words[0].lower())
    if program.endswith(".exe"):
        program = program[:-4]
    mode = WRITE_COMMANDS.get(program)
    if mode is None:
        return []
    arguments = [word for word in words[1:] if not word.startswith("-")]
    if not arguments:
        return []
    return arguments if mode == "all" else arguments[-1:]


def check_writes(
    command: str,
    workspace: str | os.PathLike[str] | None = None,
    allowed: list[Path] | None = None,
) -> dict | None:
    """Refuse an *obvious* write whose destination leaves workspace + whitelist.

    Same rule as the file toolset, and the same refusal text. It looks at
    redirections and at the handful of commands whose arguments are paths; a
    program that writes on its own (``python -c``, an editor, a script the model
    just created) is out of reach, and the module docstring says so.
    """
    segments, targets = _scan_command(command)
    destinations = list(targets)
    for segment in segments:
        destinations.extend(_destinations(segment))

    for destination in destinations:
        if destination in NULL_TARGETS or destination.startswith("&"):
            continue  # 2>&1 or > /dev/null: nothing lands on the filesystem
        candidate = Path(destination).expanduser()
        if workspace is not None and not candidate.is_absolute():
            candidate = Path(workspace) / candidate
        if sandbox.is_allowed(candidate, workspace, allowed):
            continue
        return {
            "error": sandbox.outside_message(destination, workspace, allowed),
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
        self.allowed = list(allowed) if allowed is not None else sandbox.allowed_paths(self.workspace)

    def set_workspace(self, workspace: str | os.PathLike[str]) -> None:
        """Move the shell's cwd and the write whitelist with it."""
        self.workspace = Path(workspace).expanduser().resolve()
        self.allowed = sandbox.allowed_paths(self.workspace)

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

        # The destination check runs before the shell sees the line: a refused
        # write must not have created anything on the way.
        escape = check_writes(command, self.workspace, self.allowed)
        if escape:
            return escape

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
