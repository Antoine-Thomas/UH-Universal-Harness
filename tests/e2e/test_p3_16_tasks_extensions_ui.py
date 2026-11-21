"""Porte D, preuves d'interface (D-6, D-8) : ce que l'opérateur fait, pas ce que la route sait faire.

Les deux critères de la matrice parlent du cockpit : « déclenchement manuel depuis
l'interface avec sa sortie » et « refus expliqué quand une extension est mal configurée ».
Les tests cliquent donc les boutons et lisent ce qui est affiché, jamais un appel direct.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import uh.schedule as uh_schedule

pytestmark = pytest.mark.e2e

from .conftest import Server, free_port, open_app  # noqa: E402


def _ouvrir(page, groupe: str, onglet: str) -> None:
    """Ouvre le groupe de panneaux demandé, puis son onglet."""
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.click(f"#{groupe}")
    page.click(f"#{onglet}")


@pytest.fixture()
def home_d6(make_server, tmp_path: Path):
    startup = tmp_path / "startup-jetable"
    startup.mkdir()
    return make_server(
        "home-d6",
        extra_env={
            "UH_SCHEDULE_DISABLE_SYSTEM": "1",
            "UH_SCHEDULE_STARTUP_DIR": str(startup),
        },
    )


def test_the_run_button_runs_and_shows_the_output(browser, home_d6, monkeypatch) -> None:
    """D-6 : « Lancer maintenant » déclenche la tâche et la sortie revient dans la ligne.

    Le test clique le bouton comme un opérateur, attend que l'indicateur dise `: ok`, puis
    exige deux choses dans le rendu : un dernier passage daté (la ligne ne dit plus
    « jamais lancée ») et le détail de la commande en infobulle - c'est-à-dire la sortie
    réelle, pas un simple accusé de réception.
    """
    monkeypatch.setenv("UH_HOME", str(home_d6.home))
    monkeypatch.setenv("UH_SCHEDULE_DISABLE_SYSTEM", "1")
    tache = uh_schedule.create("Diagnostic Porte D", "doctor", 15)["task"]

    context, page = open_app(browser, home_d6)
    try:
        _ouvrir(page, "group-tools", "tab-tasks")
        page.wait_for_selector(f'.task-row[data-task-id="{tache["id"]}"]', timeout=20000)

        ligne = f'.task-row[data-task-id="{tache["id"]}"]'
        assert "jamais lancée" in page.inner_text(ligne + " .task-last"), "état de départ"

        page.click(ligne + ' [data-action="run"]')
        page.wait_for_function(
            "() => document.querySelector('#tasks-hint').textContent.includes(': ok')",
            timeout=120000,
        )
        page.wait_for_function(
            f"() => !document.querySelector('{ligne} .task-last').textContent"
            ".includes('jamais lancée')",
            timeout=30000,
        )

        dernier = page.inner_text(ligne + " .task-last")
        assert dernier.startswith("dernier passage : "), dernier
        assert "ok" in dernier, dernier
        detail = page.get_attribute(ligne + " .task-last", "title") or ""
        assert "doctor" in detail, (
            f"la sortie de la commande doit revenir dans la ligne : {detail!r}"
        )
        assert "prochaine : " in page.inner_text(ligne + " .task-next")
    finally:
        context.close()


def test_a_malformed_mcp_entry_is_explained_not_hidden(browser, tmp_path: Path) -> None:
    """D-8 : une extension mal configurée s'affiche cassée, avec sa raison nommée.

    Le fichier de configuration est écrit avant le démarrage du serveur et l'entrée n'a
    pas de `command`. Le panneau doit montrer la ligne, l'étiquette « cassée » et la phrase
    du refus - pas une liste vide ni une erreur de page.
    """
    server = Server(tmp_path / "home-d8", free_port())
    server.home.mkdir(parents=True, exist_ok=True)
    (server.home / "config.toml").write_text(
        "# `command` manque volontairement : c'est le cas que D-8 doit expliquer.\n"
        "[[mcp.servers]]\n"
        'name = "casse"\n'
        'args = ["-y", "rien"]\n',
        encoding="utf-8",
        newline="\n",
    )
    server.start()
    try:
        context, page = open_app(browser, server)
        try:
            _ouvrir(page, "group-tools", "tab-extensions")
            page.wait_for_selector('.extension-row[data-kind="mcp"]', timeout=20000)

            ligne = page.locator('.extension-row[data-kind="mcp"][data-name="casse"]')
            assert ligne.count() == 1, "l'entrée mal configurée doit être listée"
            assert ligne.get_attribute("data-state") == "broken"

            texte = ligne.inner_text()
            assert "cassée" in texte, texte
            assert "configuration refusée avant tout démarrage" in texte, texte
            assert "'command' is required" in texte, texte
        finally:
            context.close()
    finally:
        server.stop()


def test_testing_a_server_whose_binary_is_missing_names_the_cause(browser, tmp_path: Path) -> None:
    """D-8, second chemin : le binaire n'existe pas, l'épreuve dit lequel et pourquoi.

    Une entrée bien formée mais pointant sur un exécutable absent n'est pas « cassée » à
    l'inventaire : le serveur ne démarre qu'au premier appel ou à l'épreuve. C'est donc le
    bouton « Tester » qui doit nommer la cause - « binaire introuvable » - et pas répondre
    « erreur » ni laisser la ligne muette.
    """
    server = Server(tmp_path / "home-d8-bin", free_port())
    server.home.mkdir(parents=True, exist_ok=True)
    fantome = str(tmp_path / "nexistepas" / "nexistepas.exe")
    (server.home / "config.toml").write_text(
        "[[mcp.servers]]\n"
        'name = "fantome"\n'
        f'command = "{fantome.replace(os.sep, "/")}"\n'
        "args = []\n",
        encoding="utf-8",
        newline="\n",
    )
    server.start()
    try:
        context, page = open_app(browser, server)
        try:
            _ouvrir(page, "group-tools", "tab-extensions")
            ligne = '.extension-row[data-kind="mcp"][data-name="fantome"]'
            page.wait_for_selector(ligne, timeout=20000)

            page.click(ligne + ' [data-action="test"]')
            page.wait_for_function(
                "() => document.querySelector('#extensions-hint').textContent"
                ".includes('binaire introuvable')",
                timeout=60000,
            )

            hint = page.inner_text("#extensions-hint")
            assert "refusé" in hint, hint
            assert "binaire introuvable" in hint, hint
            assert "nexistepas.exe" in hint, hint
            # L'inventaire, lui, garde la ligne : une épreuve qui échoue ne l'efface pas.
            assert page.locator(ligne).count() == 1
        finally:
            context.close()
    finally:
        server.stop()
