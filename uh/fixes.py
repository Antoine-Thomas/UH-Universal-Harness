"""Propose a patch, prove it applies, and never touch a tree you did not approve.

The expensive failure mode of a "self-repairing" agent is not a bad idea: it is a
patch that *looks* like a patch. A model asked for a unified diff will happily
return a diff whose context lines do not exist, and `git apply` then fails - or
worse, half-applies. So nothing here trusts the text:

1. the answer must start with ``diff --git`` (anything else is not a patch);
2. it must pass ``git apply --check`` before it is even shown as applicable;
3. applying happens on a new branch, and a failed check runs the patch backwards;
4. there is no ``commit`` and no ``push`` anywhere in this module, on purpose.

A human reads the diff. A human merges it. That is the brake.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .config import uh_home

DIFF_MARKER = "diff --git"
MAX_PATCH_BYTES = 400_000


class FixError(RuntimeError):
    """A repair that cannot be proposed or applied safely. Always readable."""


@dataclass
class Proposal:
    stamp: str
    instruction: str
    diff: str
    path: Path
    applies: bool
    check_error: str = ""
    notes: List[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "stamp": self.stamp,
            "instruction": self.instruction,
            "path": str(self.path),
            "applies": self.applies,
            "error": self.check_error,
            "files": sorted(
                {line.split(" b/")[-1][2:].strip() for line in self.diff.splitlines() if line.startswith("+++ b/")}
            ),
            "notes": self.notes,
        }


def _git(repo: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(repo), *args]
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FixError(f"cannot run {' '.join(shlex.quote(part) for part in command)}: {exc}") from exc


def repo_root(start: Optional[Path] = None) -> Path:
    """The git top level, or a refusal: a repair outside a repository is a guess."""
    here = start or Path.cwd()
    result = _git(here, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise FixError(f"{here} is not inside a git repository: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def fixes_dir() -> Path:
    return uh_home() / "fixes"


def new_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def check(repo: Path, diff: str) -> Tuple[bool, str]:
    """``git apply --check``: does this patch actually fit this tree?

    The check runs against a temporary file, so a patch that does not apply leaves
    nothing behind - not even a rejected hunk file.
    """
    if len(diff) > MAX_PATCH_BYTES:
        return False, f"patch is {len(diff)} bytes, over the {MAX_PATCH_BYTES} limit"
    if DIFF_MARKER not in diff:
        return False, "the answer is not a unified diff (it has no 'diff --git' line)"
    candidate = fixes_dir() / f".check-{new_stamp()}.diff"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        # newline="\n": Path.write_text translates to CRLF on Windows, and a patch
        # whose lines end in \r does not apply - git compares the \r as content.
        candidate.write_text(
            diff if diff.endswith("\n") else diff + "\n", encoding="utf-8", newline="\n"
        )
        result = _git(repo, "apply", "--check", str(candidate))
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout).strip().splitlines()[0] if (
            result.stderr or result.stdout
        ) else "git apply --check refused the patch"
    finally:
        candidate.unlink(missing_ok=True)


def propose(
    instruction: str,
    *,
    repo: Optional[Path] = None,
    patch: Optional[str] = None,
    provider: object = None,
    model: str = "",
) -> Proposal:
    """Ask for a patch (or take one given) and record whether it applies."""
    root = repo_root(repo)
    stamp = new_stamp()
    notes: List[str] = []
    text = (patch or "").strip()
    if not text:
        text, notes = _ask_model(instruction, root, provider=provider, model=model)
    path = fixes_dir() / f"{stamp}.diff"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        text + ("\n" if text and not text.endswith("\n") else ""),
        encoding="utf-8",
        newline="\n",
    )
    applies, error = (False, "empty answer") if not text else check(root, text)
    return Proposal(
        stamp=stamp,
        instruction=instruction,
        diff=text,
        path=path,
        applies=applies,
        check_error=error,
        notes=notes,
    )


def _ask_model(
    instruction: str, repo: Path, *, provider: object = None, model: str = ""
) -> Tuple[str, List[str]]:
    """One turn, one instruction, and no tool access: a patch, or nothing."""
    notes: List[str] = []
    if provider is None:
        try:
            from .providers import build_provider

            provider = build_provider("", model or None)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - no provider is a normal state
            raise FixError(
                "no provider to ask for a patch: pass --from-file <patch.diff>, or "
                f"configure a provider first ({type(exc).__name__}: {exc})"
            ) from exc

    context = _git(repo, "ls-files").stdout.splitlines()[:400]
    system = (
        "You are a careful maintainer. Answer with ONE unified diff and nothing else: "
        "it must start with 'diff --git a/<path> b/<path>', contain valid context lines "
        "from the real file, and change as little as possible. No prose, no markdown "
        "fences, no explanation. If you cannot produce a diff that applies, answer "
        "exactly: CANNOT."
    )
    user = (
        "Repository files (truncated):\n" + "\n".join(context) + f"\n\nProblem:\n{instruction}"
    )
    try:
        answer = provider.chat(  # type: ignore[attr-defined]
            [{"role": "system", "content": system}, {"role": "user", "content": user}], []
        )
    except Exception as exc:  # noqa: BLE001 - the model refusing is data
        raise FixError(f"the model call failed: {type(exc).__name__}: {exc}") from exc
    text = _text_of(answer)
    if "CANNOT" in text and DIFF_MARKER not in text:
        raise FixError("the model answered CANNOT: it has no patch it trusts")
    if DIFF_MARKER not in text:
        notes.append("the answer contained prose; only the diff part was kept")
        index = text.find("```diff")
        if index >= 0:
            text = text[index + len("```diff") :].split("```")[0]
    start = text.find(DIFF_MARKER)
    if start > 0:
        text = text[start:]
    return text.strip(), notes


def _text_of(answer: object) -> str:
    if isinstance(answer, dict):
        for key in ("content", "text", "message"):
            value = answer.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                inner = value.get("content")
                if isinstance(inner, str):
                    return inner
    return str(answer or "")


def apply(
    proposal: Proposal,
    *,
    repo: Optional[Path] = None,
    branch: Optional[str] = None,
    checks: Sequence[Sequence[str]] = (),
) -> dict:
    """Apply on a branch, run the checks, and undo the patch if a check fails.

    Deliberately absent: ``git commit``, ``git push``. The tree ends up dirty on a
    branch named ``fix/auto-<stamp>`` and the operator decides what happens next.
    """
    if not proposal.applies:
        raise FixError(f"refusing to apply: {proposal.check_error or 'the patch does not apply'}")
    root = repo_root(repo)
    target = branch or f"fix/auto-{proposal.stamp}"
    created = _git(root, "checkout", "-b", target)
    if created.returncode != 0:
        raise FixError(f"cannot create branch {target}: {created.stderr.strip()}")
    applied = _git(root, "apply", str(proposal.path))
    if applied.returncode != 0:
        _git(root, "checkout", "-")
        raise FixError(f"git apply failed after --check passed: {applied.stderr.strip()}")

    failed: List[str] = []
    for command in checks:
        result = subprocess.run(
            list(command), cwd=str(root), capture_output=True, text=True, timeout=900
        )
        if result.returncode != 0:
            failed.append(
                f"{' '.join(command)}: "
                + ((result.stdout or result.stderr).strip().splitlines() or ["failed"])[-1]
            )
    if failed:
        # Fail closed: a patch that breaks the suite is not a repair. The tree goes
        # back to HEAD; the branch stays so the operator can look at the diff.
        _git(root, "checkout", "--", ".")
        return {
            "applied": False,
            "branch": target,
            "reverted": True,
            "failed": failed,
            "diff": str(proposal.path),
        }
    status = _git(root, "status", "--porcelain").stdout.strip().splitlines()
    return {
        "applied": True,
        "branch": target,
        "reverted": False,
        "changed": status,
        "diff": str(proposal.path),
        "next": "review with `git diff`, then commit; nothing was committed or pushed",
    }


__all__ = [
    "DIFF_MARKER",
    "FixError",
    "Proposal",
    "apply",
    "check",
    "fixes_dir",
    "new_stamp",
    "propose",
    "repo_root",
]
