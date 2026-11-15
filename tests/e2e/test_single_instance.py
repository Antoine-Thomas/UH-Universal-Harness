"""E4/E5: one instance per host, and a cold start that works the first time.

These two are the ones that made the product look "99 % broken": the launcher
silently started a second server on the next port (older routes, newer files), and
a cold start that fails once makes the user reload until it works.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from .conftest import PYTHON, ROOT, Server, free_port, open_app

pytestmark = pytest.mark.e2e


def test_e5_a_second_launch_does_not_create_a_second_server(tmp_path: Path, server: Server) -> None:
    """INVARIANT: one `uh web` per port; a second one says so and exits 0.

    The old behaviour moved to the next free port without a word: the browser then
    talked to an older process while loading newer files, which is where "the tab
    does not exist" and the 404 favicon came from.
    """
    env = dict(os.environ, UH_HOME=str(server.home), PYTHONUNBUFFERED="1")
    second = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "uh",
            "web",
            "--no-open",
            "--no-uhb",
            "--port",
            str(server.port),
        ],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    output = (second.stdout or "") + (second.stderr or "")

    assert second.returncode == 0, output[-800:]
    assert "sert déjà UH" in output, output[-800:]
    assert "8766" not in output and str(server.port + 1) not in output, (
        "no silent move to the next port: " + output[-400:]
    )
    # And the first server is still the one answering.
    assert httpx.get(server.base + "/healthz", timeout=5).json()["app"] == "uh"


def test_e4_ten_cold_starts_all_reach_a_usable_ui(tmp_path: Path, browser) -> None:
    """INVARIANT: the first launch works; the user never has to reload to get in.

    Ten fresh homes, ten fresh ports, ten boots: each one must answer /healthz,
    authenticate, and leave a wired composer. The table is printed for the report.
    """
    rows: list[str] = []
    failures: list[str] = []
    for attempt in range(1, 11):
        server = Server(tmp_path / f"cold{attempt}", free_port())
        try:
            server.start()
            health = httpx.get(server.base + "/healthz", timeout=10).json()
            status = httpx.get(
                server.base + "/api/status",
                headers={"Authorization": f"Bearer {server.token()}"},
                timeout=10,
            )
            context, page = open_app(browser, server)
            composer = page.locator("#input").count()
            send_enabled = page.locator("#send").is_enabled()
            context.close()
            ok = health.get("app") == "uh" and status.status_code == 200 and composer == 1
            rows.append(
                f"  {attempt:2d}  port={server.port}  healthz={health.get('app')}  "
                f"status={status.status_code}  composer={composer}  send_enabled={send_enabled}"
            )
            if not ok:
                failures.append(f"attempt {attempt}: healthz={health} status={status.status_code}")
        finally:
            server.stop()
    print("E4 - 10 démarrages à froid :\n" + "\n".join(rows))
    assert not failures, "\n".join(failures)
