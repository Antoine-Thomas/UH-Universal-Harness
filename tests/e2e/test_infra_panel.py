"""La vue Infra à l'écran (P3-16c lot 3) : rouge quand un service manque, lecture seule.

Le serveur de test est lancé avec un `UH_UHB_URL` sur un port fermé et un dossier de runs
jetable : la vue doit donc afficher une erreur explicite et des tailles qui viennent de CE
dossier, pas d'une supposition.

    pytest -o addopts="" -m e2e tests/e2e/test_infra_panel.py -q
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture()
def runs(tmp_path: Path) -> Path:
    root = tmp_path / "runs-jetables"
    (root / "run-a").mkdir(parents=True)
    (root / "run-a" / "un.bin").write_bytes(b"a" * 2048)
    (root / "run-a" / "deux.bin").write_bytes(b"b" * 1024)
    return root


def open_infra(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.click("#group-tools")
    page.click("#tab-infra")
    page.wait_for_selector("#infra-services .infra-service", timeout=25000)


def test_a_dead_second_brain_shows_red_with_a_reason(browser, make_server, runs: Path) -> None:
    server = make_server(
        extra_env={"UH_UHB_URL": f"http://127.0.0.1:{closed_port()}", "UH_UHB_RUNS": str(runs)}
    )
    context, page = open_app(browser, server)
    try:
        open_infra(page)
        uhb = page.eval_on_selector(
            "#infra-services .infra-service[data-service='UHB']",
            "el => ({ state: el.dataset.state, text: el.innerText })",
        )
        assert uhb["state"] == "down", uhb
        assert "UHB" in uhb["text"]
        assert "port" in uhb["text"].lower(), f"la raison nomme le port : {uhb['text']!r}"

        alerts = page.eval_on_selector_all(
            "#infra-alerts .infra-alert", "els => els.map(e => [e.dataset.level, e.innerText])"
        )
        assert any(level == "error" for level, _ in alerts), alerts
        assert any("cerveau" in text.lower() for _, text in alerts), alerts
    finally:
        context.close()


def test_the_sizes_come_from_the_real_folders(browser, make_server, runs: Path) -> None:
    server = make_server(extra_env={"UH_UHB_RUNS": str(runs)})
    context, page = open_app(browser, server)
    try:
        open_infra(page)
        disk = page.eval_on_selector("#infra-disk", "el => el.innerText")
        assert "~/.uhb/runs" in disk
        assert "2 fichier(s)" in disk, f"le dossier jetable contient deux fichiers : {disk!r}"
        assert "3 kB" in disk, f"3072 octets doivent s'afficher en kB : {disk!r}"
        assert "absent" not in disk.split("~/.uhb/runs")[1][:60], disk
    finally:
        context.close()


def test_the_logs_are_collapsed_and_masked(browser, make_server) -> None:
    server = make_server()
    # Un jeton écrit dans le journal du serveur ne doit pas ressortir.
    (server.home / "uh-web.log").write_text(
        'GET /?token=DO_NOT_LEAK_ME HTTP/1.1" 200\n', encoding="utf-8"
    )
    context, page = open_app(browser, server)
    try:
        open_infra(page)
        page.click("#infra-refresh")
        page.wait_for_timeout(1500)
        logs = page.eval_on_selector_all(
            "#infra-logs details.infra-log", "els => els.map(e => e.dataset.name)"
        )
        assert "uh-web.log" in logs, f"journaux listés : {logs}"
        body = page.eval_on_selector("#infra-logs", "el => el.innerText")
        assert "DO_NOT_LEAK_ME" not in body, "un jeton est sorti dans la vue"
        # Le contenu n'apparaît qu'une fois le bloc déplié : c'est borné et discret.
        page.click("#infra-logs details.infra-log summary")
        page.wait_for_timeout(300)
        body = page.eval_on_selector("#infra-logs", "el => el.innerText")
        assert "token=***" in body
    finally:
        context.close()


def test_the_view_offers_no_action(browser, make_server) -> None:
    server = make_server()
    context, page = open_app(browser, server)
    try:
        open_infra(page)
        buttons = page.eval_on_selector_all(
            "#infra-view button", "els => els.map(e => e.textContent.trim().toLowerCase())"
        )
        assert buttons == ["actualiser"], f"aucun bouton d'action : {buttons}"
        html = page.eval_on_selector("#infra-view", "el => el.innerHTML.toLowerCase()")
        for forbidden in ("arrêter", "arreter", "redémarrer", "redemarrer", "kill", "supprimer"):
            assert forbidden not in html, f"« {forbidden} » ne doit pas exister dans la vue"
    finally:
        context.close()
