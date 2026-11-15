"""Repro (standalone): two `uh web` instances on one host share the uh_token cookie.

Origin: written by an external reviewer and run under Linux against 1.7.0. Kept as
a runnable record; the pytest port lives in tests/e2e/test_ux_invariants.py (E3).
Run: python tests/e2e/repro/two_instances.py <scratch_dir>
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parents[3]
CANDIDATES = [
    HERE / ".venv" / "Scripts" / "uh.exe",
    HERE / ".venv" / "bin" / "uh",
]
UH = next((str(path) for path in CANDIDATES if path.is_file()), "uh")

S = sys.argv[1] if len(sys.argv) > 1 else "."
env = dict(os.environ, UH_HOME=f"{S}/repro-home", PYTHONUNBUFFERED="1")
procs, urls = [], {}
for port in (8765, 8766):
    proc = subprocess.Popen(
        [UH, "web", "--no-open", "--no-uhb", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    procs.append(proc)
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        match = re.search(rf"(http://127\.0\.0\.1:{port}/\?token=\S+)", line or "")
        if match:
            urls[port] = match.group(1)
            break
    else:
        print("no URL for", port)

try:
    print("instances up:", sorted(urls))
    browser = httpx.Client()  # one cookie jar, like one browser profile

    def status(port: int) -> int:
        return browser.get(f"http://127.0.0.1:{port}/api/status").status_code

    print("open 8765 with its token      ->", browser.get(urls[8765]).status_code)
    print("tab 8765 /api/status          ->", status(8765))
    print("open 8766 with its token      ->", browser.get(urls[8766]).status_code)
    print("tab 8766 /api/status          ->", status(8766))
    print("tab 8765 /api/status (stale)  ->", status(8765), "<-- the old tab")
    print("cookies:", [(c.name, c.domain, c.path) for c in browser.cookies.jar])
finally:
    for proc in procs:
        proc.terminate()
    for proc in procs:
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001 - best effort teardown
            proc.kill()
