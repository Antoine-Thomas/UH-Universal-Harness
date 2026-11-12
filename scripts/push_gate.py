"""Pre-push gate: ruff, mypy and the full test suite, in that order.

The hook exists because a red commit that reaches CI is already published: on a
tag it would even be a release candidate. The gate stops at the first failure and
the push is refused.

Two details that matter on this project:

* ``UH_HOME`` is stripped from the child environment. A shell that exported it
  for a manual demo would otherwise leak into the test suite, and some tests
  spawn ``python -m uh`` which inherits it (that mistake once made a green test
  look red).
* nothing here prints or logs a key: the three commands never echo secrets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str) -> list[str]:
    """Prefer the project venv, then PATH, then ``python -m``."""
    for candidate in (
        ROOT / ".venv" / "Scripts" / f"{name}.exe",
        ROOT / ".venv" / "bin" / name,
    ):
        if candidate.exists():
            return [str(candidate)]
    found = shutil.which(name)
    if found:
        return [found]
    return [sys.executable, "-m", name]


COMMANDS: list[tuple[str, list[str]]] = [
    ("ruff", _tool("ruff") + ["check", "uh", "tests", "scripts"]),
    ("mypy", _tool("mypy")),
    ("pytest", _tool("pytest") + ["-q"]),
]


def main() -> int:
    env = {key: value for key, value in os.environ.items() if key != "UH_HOME"}
    for name, command in COMMANDS:
        print(f"[push-gate] {name} …", flush=True)
        result = subprocess.run(command, cwd=str(ROOT), env=env)
        if result.returncode != 0:
            print(
                f"[push-gate] {name} failed (exit {result.returncode}) — push refused",
                file=sys.stderr,
            )
            return result.returncode
    print("[push-gate] ruff, mypy and pytest are green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
