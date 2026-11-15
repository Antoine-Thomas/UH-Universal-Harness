"""End-to-end harness: a real ``uh web``, a real Chromium, a throwaway UH_HOME.

Nothing here touches ``~/.uh``: every server gets its own home under ``tmp_path``,
its own free port, and ``--no-uhb``. The tests stop at what the UI and the server
do between them - they never ask a model for an answer, so no key, no GPU and no
network are involved.

Playwright is a TEST dependency only; it is imported lazily so a checkout without
it still runs the rest of the suite.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: Which interpreter runs `uh web`, and from where. Default: this checkout's venv,
#: started inside the checkout (an editable install). A release check points them at
#: a fresh venv and a neutral folder, so the wheel - not the checkout - is under test:
#:   UH_E2E_PYTHON=/tmp/v/bin/python UH_E2E_CWD=/tmp pytest -m e2e
_CANDIDATES = [
    Path(os.environ["UH_E2E_PYTHON"]) if os.environ.get("UH_E2E_PYTHON") else None,
    ROOT / ".venv" / "Scripts" / "python.exe",  # Windows
    ROOT / ".venv" / "bin" / "python",  # POSIX
]
PYTHON = next((path for path in _CANDIDATES if path and path.is_file()), Path(sys.executable))
CWD = Path(os.environ.get("UH_E2E_CWD") or ROOT)

pw_api = pytest.importorskip("playwright.sync_api", reason="e2e needs the test-only playwright")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Server:
    """One ``uh web`` process, started and stopped by the test."""

    def __init__(self, home: Path, port: int) -> None:
        self.home = home
        self.port = port
        self.url = ""
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Server":
        self.home.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, UH_HOME=str(self.home), PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uh",
                "web",
                "--no-open",
                "--no-uhb",
                "--port",
                str(self.port),
            ],
            cwd=str(CWD),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.time() + 45
        assert self.proc.stdout is not None
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                break
            self.lines.append(line)
            match = re.search(rf"(http://127\.0\.0\.1:{self.port}/\?token=\S+)", line)
            if match:
                self.url = match.group(1)
                return self
        raise AssertionError("server never printed its URL:\n" + "".join(self.lines[-12:]))

    def token(self) -> str:
        return self.url.split("token=", 1)[1]

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                self.proc.kill()
        self.proc = None


@pytest.fixture()
def make_server(tmp_path: Path):
    started: list[Server] = []

    def factory(name: str = "home", port: int | None = None) -> Server:
        server = Server(tmp_path / name, port or free_port())
        started.append(server)
        return server.start()

    yield factory
    for server in started:
        server.stop()


@pytest.fixture()
def server(make_server) -> Server:
    return make_server()


@pytest.fixture()
def browser():
    with pw_api.sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


def open_app(browser, server: Server):
    """A page on the app, with its own cookie jar, waiting for the composer."""
    context = browser.new_context()
    page = context.new_page()
    page.goto(server.url, wait_until="domcontentloaded")
    page.wait_for_selector("#input", timeout=30000)
    # The shell wires itself after the first paint: give boot() a moment so the
    # composer is the wired one, not just the markup.
    page.wait_for_timeout(1500)
    return context, page
