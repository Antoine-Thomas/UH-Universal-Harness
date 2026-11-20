"""La section Tâches à l'écran (P3-16c lot 4) : créer, lancer, désactiver, supprimer.

Le serveur de test tourne avec les déclencheurs neutralisés : aucun test n'écrit dans le
vrai dossier Démarrage de l'opérateur, et ce que l'on prouve est le comportement du
cockpit, pas celui de Windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


@pytest.fixture()
def server_no_triggers(make_server, tmp_path: Path):
    startup = tmp_path / "startup-jetable"
    startup.mkdir()
    return make_server(
        extra_env={
            "UH_SCHEDULE_DISABLE_SYSTEM": "1",
            "UH_SCHEDULE_STARTUP_DIR": str(startup),
        }
    )


def open_tasks(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.click("#group-tools")
    page.click("#tab-tasks")
    page.wait_for_selector("#tasks-list", timeout=20000)


def test_the_catalog_is_shown_and_no_free_text_command_exists(browser, server_no_triggers) -> None:
    context, page = open_app(browser, server_no_triggers)
    try:
        open_tasks(page)
        page.wait_for_selector("#tasks-catalog .tasks-catalog-line", timeout=20000)
        keys = page.eval_on_selector_all("#tasks-catalog .tasks-key", "els => els.map(e => e.textContent.trim())")
        assert keys == ["doctor", "dream", "runs-prune"], keys
        # La commande est un <select> rempli par le serveur : pas de champ libre.
        assert page.eval_on_selector("#task-command", "el => el.tagName") == "SELECT"
        options = page.eval_on_selector_all("#task-command option", "els => els.map(e => e.value)")
        assert options == ["doctor", "dream", "runs-prune"]
        mode = page.eval_on_selector("#tasks-mode", "el => el.textContent")
        assert "déclencheurs désactivés" in mode, mode
    finally:
        context.close()


def test_create_run_toggle_delete_from_the_panel(browser, server_no_triggers) -> None:
    context, page = open_app(browser, server_no_triggers)
    page.on("dialog", lambda dialog: dialog.accept())
    try:
        open_tasks(page)
        page.wait_for_selector("#tasks-catalog .tasks-catalog-line", timeout=20000)

        page.fill("#task-name", "e2e diagnostic")
        page.select_option("#task-command", "doctor")
        page.fill("#task-every", "15")
        page.click("#task-create")
        page.wait_for_selector("#tasks-list .task-row", timeout=20000)
        row = "#tasks-list .task-row"
        assert page.eval_on_selector(f"{row} .task-name", "el => el.textContent") == "e2e diagnostic"
        assert "activée" in page.eval_on_selector(f"{row} .task-flag", "el => el.textContent")
        assert "15 min" in page.eval_on_selector(f"{row} .task-every", "el => el.textContent")
        assert page.eval_on_selector(f"{row} .task-last", "el => el.textContent") == "jamais lancée"

        # Lancer maintenant : le doctor tourne vraiment, l'horodatage apparaît.
        page.click(f"{row} button[data-action='run']")
        page.wait_for_function(
            "() => { const el = document.querySelector('#tasks-list .task-row .task-last');"
            " return el && el.textContent.includes('dernier passage'); }",
            timeout=90000,
        )
        last = page.eval_on_selector(f"{row} .task-last", "el => el.textContent")
        assert "ok" in last, f"le statut du passage est affiché : {last!r}"
        hint = page.eval_on_selector("#tasks-hint", "el => el.textContent")
        assert "ok" in hint.lower(), hint

        # Désactiver, puis supprimer.
        page.click(f"{row} button[data-action='toggle']")
        page.wait_for_function(
            "() => { const el = document.querySelector('#tasks-list .task-row .task-flag');"
            " return el && el.textContent.includes('désactivée'); }",
            timeout=20000,
        )
        page.click(f"{row} button[data-action='delete']")
        page.wait_for_selector("#tasks-list .tasks-empty", timeout=20000)
        assert page.eval_on_selector("#tasks-list", "el => el.innerText").strip().startswith("Aucune tâche")
    finally:
        context.close()


def test_the_registry_survives_a_reload(browser, server_no_triggers) -> None:
    """Une tâche créée reste dans le registre : elle est lue depuis le disque, pas d'un état
    de page."""
    context, page = open_app(browser, server_no_triggers)
    page.on("dialog", lambda dialog: dialog.accept())
    try:
        open_tasks(page)
        page.wait_for_selector("#tasks-catalog .tasks-catalog-line", timeout=20000)
        page.fill("#task-name", "persistante")
        page.select_option("#task-command", "runs-prune")
        page.fill("#task-every", "240")
        page.click("#task-create")
        page.wait_for_selector("#tasks-list .task-row", timeout=20000)
    finally:
        context.close()

    context, page = open_app(browser, server_no_triggers)
    try:
        open_tasks(page)
        page.wait_for_selector("#tasks-list .task-row", timeout=20000)
        names = page.eval_on_selector_all("#tasks-list .task-name", "els => els.map(e => e.textContent)")
        assert "persistante" in names, names
        registry = (server_no_triggers.home / "schedules.json").read_text(encoding="utf-8")
        assert "persistante" in registry
    finally:
        context.close()
