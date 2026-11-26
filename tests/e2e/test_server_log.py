"""Le journal du serveur e2e doit exister : la CI archive `**/uh-web.log`.

L'etape d'artefact de `ci.yml` cherchait un fichier que rien n'ecrivait, d'ou
« No files were found » au moment precis ou l'on en avait besoin : quand un test
e2e echoue, le journal du serveur est la seule source qui dise pourquoi.

Le journal est ecrit dans le depot (`.pytest-e2e/uh-web.log`), pas dans le dossier
temporaire : un artefact `**/uh-web.log` ne cherche que dans l'espace de travail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from tests.e2e.conftest import LOG_DIR, LOG_NAME, Server  # noqa: E402


def test_the_server_writes_a_log_file(tmp_path: Path) -> None:
    server = Server(tmp_path / "home", 8899)
    server.start()
    try:
        log = LOG_DIR / LOG_NAME
        assert log.is_file(), f"aucun journal ecrit : {log}"
        content = log.read_text(encoding="utf-8", errors="replace")
        assert "http://127.0.0.1:8899" in content, "le journal doit contenir l'URL du serveur"
    finally:
        server.stop()
