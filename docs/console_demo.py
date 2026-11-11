"""Capture the console path without a real TTY.

The pty of the build environment only delivers the newline (a bare
``input()`` receives nothing either - measured), so this harness feeds the
REPL through a ``StringIO`` that *reports itself as a TTY*: ``cmd_console``
then takes exactly the interactive branch (banner, status bar, slash commands,
persistence) with the real code. Only the terminal is simulated.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

os.environ["UH_HOME"] = tempfile.mkdtemp(prefix="uh-console-demo-")
os.environ.pop("UH_NO_SAVE", None)

SCRIPTED = ["/help", "/model", "/status", "/tools", "/exit"]


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def main() -> int:
    from uh import cli

    sys.stdin = FakeTty("\n".join(SCRIPTED) + "\n")
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    try:
        code = cli.main([])
    finally:
        out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__

    print("=== stderr (banner, statut, annonces) ===")
    print(err.rstrip())
    print()
    print("=== stdout (/help puis /model puis /status puis /tools) ===")
    print(out.rstrip())
    print()
    print(f"=== exit code: {code} ===")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
