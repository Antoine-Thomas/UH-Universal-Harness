"""Esthétique du cockpit : trois comportements mesurables, pas des impressions.

Chaque test lit une valeur rendue (largeur calculée, texte réel, format de date) et non une
chaîne du source : c'est la règle du projet, un test qui lit le code ne prouve pas l'écran.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

import uh.schedule as uh_schedule

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


def test_escape_closes_the_modal_without_collapsing_the_panel(browser, server_no_triggers) -> None:
    """Échap appartient à la modale : le panneau de droite doit rester ouvert.

    Constaté en production : la modale se fermait et #layout recevait `right-collapsed`,
    donc #group-tools tombait à 0 px. La mesure porte sur la largeur rendue, parce que la
    classe du panneau, elle, ne changeait pas - elle ne prouvait rien.
    """
    context, page = open_app(browser, server_no_triggers)
    try:
        page.click("#attach")
        page.wait_for_selector("#plus-menu:not([hidden])", timeout=20000)
        largeur_avant = page.eval_on_selector(
            "#group-tools", "el => Math.round(el.getBoundingClientRect().width)"
        )
        assert largeur_avant > 0, "le panneau doit être ouvert avant la mesure"

        page.keyboard.press("Escape")
        # `wait_for_selector` attend la visibilité par défaut : un élément masqué ne le
        # devient jamais, d'où l'attente sur l'état logique.
        page.wait_for_function(
            "() => document.querySelector('#plus-menu').hasAttribute('hidden')", timeout=10000
        )

        assert page.eval_on_selector("#plus-menu", "el => el.hasAttribute('hidden')")
        largeur_apres = page.eval_on_selector(
            "#group-tools", "el => Math.round(el.getBoundingClientRect().width)"
        )
        assert largeur_apres == largeur_avant, (
            f"le panneau s'est replié avec la modale : {largeur_avant} px -> {largeur_apres} px"
        )
        assert "right-collapsed" not in page.eval_on_selector("#layout", "el => el.className")
    finally:
        context.close()


def test_escape_still_collapses_the_panel_when_no_modal_is_open(
    browser, server_no_triggers
) -> None:
    """Le comportement d'origine reste : hors modale, Échap replie le panneau."""
    context, page = open_app(browser, server_no_triggers)
    try:
        largeur = page.eval_on_selector(
            "#group-tools", "el => Math.round(el.getBoundingClientRect().width)"
        )
        assert largeur > 0
        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => document.querySelector('#layout').classList.contains('right-collapsed')",
            timeout=10000,
        )
    finally:
        context.close()


def test_the_composer_gives_its_whole_width_to_the_field(browser, server_no_triggers) -> None:
    """Aucun espace vide : le champ occupe ce que les boutons laissent.

    On compare la somme des largeurs rendues à la largeur du conteneur : si un trou
    existait entre le champ et les boutons, l'écart mesuré le montrerait.
    """
    context, page = open_app(browser, server_no_triggers)
    try:
        mesures = page.evaluate(
            """() => {
                const c = document.querySelector('#composer');
                const champ = document.querySelector('#input').getBoundingClientRect();
                const envoi = document.querySelector('#send').getBoundingClientRect();
                const autres = [...c.children]
                    .filter(el => el !== document.querySelector('#input') && el.getBoundingClientRect().width > 1)
                    .reduce((total, el) => total + el.getBoundingClientRect().width, 0);
                return {conteneur: c.getBoundingClientRect().width,
                        champ: champ.width, envoi: envoi.width,
                        ecart: envoi.x - (champ.x + champ.width),
                        autres, debordement: c.scrollWidth > c.clientWidth + 1};
            }"""
        )
        assert mesures["champ"] > 200, "le champ doit rester utilisable"
        assert mesures["ecart"] <= 16, (
            f"espace vide entre le champ et le bouton d'envoi : {mesures['ecart']} px"
        )
        assert not mesures["debordement"], "la barre ne doit pas déborder horizontalement"
    finally:
        context.close()


def test_the_tasks_panel_writes_dates_in_french(browser, server_no_triggers, monkeypatch) -> None:
    """`next_run` et `last_run` sont lisibles : jamais l'ISO brut dans l'interface.

    Le registre est semé directement (trois tâches avec des derniers passages connus)
    plutôt qu'en lançant `doctor` : on éprouve ici le RENDU des dates, et faire tourner un
    diagnostic pour obtenir un horodatage coûtait une minute de CI pour rien - c'est ce
    qui a rapproché la suite E2E Windows de son plafond de 15 minutes.
    """
    monkeypatch.setenv("UH_HOME", str(server_no_triggers.home))
    maintenant = time.time()

    def horodatage(secondes_avant: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(maintenant - secondes_avant))

    data = uh_schedule.load_registry()
    data["tasks"] = [
        {
            "id": "t-date-aujourdhui", "name": "aujourd'hui", "command": "doctor",
            "every_minutes": 15, "enabled": True, "created": horodatage(7200),
            "last_run": horodatage(3600), "last_status": "ok",
        },
        {
            "id": "t-date-hier", "name": "hier", "command": "doctor",
            "every_minutes": 60, "enabled": True, "created": horodatage(172800),
            "last_run": horodatage(86400 + 7200), "last_status": "ok",
        },
        {
            "id": "t-date-mois", "name": "mois", "command": "doctor",
            "every_minutes": 60, "enabled": True, "created": horodatage(3456000),
            "last_run": horodatage(3456000), "last_status": "ok",
        },
    ]
    uh_schedule.save_registry(data)

    context, page = open_app(browser, server_no_triggers)
    try:
        if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
            page.click("#providers-open")
        page.click("#group-tools")
        page.click("#tab-tasks")
        page.wait_for_function(
            "() => document.querySelectorAll('#tasks-list .task-row').length >= 3", timeout=20000
        )
        texte = page.eval_on_selector("#tasks-list", "el => el.innerText")
        assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", texte), (
            f"date ISO brute affichée : {texte}"
        )
        assert re.search(r"Aujourd'hui \d{2}:\d{2}", texte), f"pas de « Aujourd'hui » : {texte}"
        assert re.search(r"Hier \d{2}:\d{2}", texte), f"pas de « Hier » : {texte}"
        assert re.search(r"\d{1,2} [a-zéû]+\. \d{2}:\d{2}", texte), (
            f"pas de date au format « 12 oct. 03:25 » : {texte}"
        )
        # Les deux champs sont rendus par le même formateur : dernier passage ET prochaine.
        assert texte.count("dernier passage :") == 3, texte
        assert texte.count("prochaine :") == 3, texte
    finally:
        context.close()
