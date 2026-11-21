"""Esthétique du cockpit : trois comportements mesurables, pas des impressions.

Chaque test lit une valeur rendue (largeur calculée, texte réel, format de date) et non une
chaîne du source : c'est la règle du projet, un test qui lit le code ne prouve pas l'écran.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
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

    # Ancrage calendaire : les dates sont posées à une heure FIXE d'un jour donné du serveur
    # (« aujourd'hui 00:05 », « hier 00:05 », « il y a 40 jours 12:00 ») au lieu de
    # « maintenant moins N secondes ». Une suite lancée entre 00:00 et 01:00 faisait
    # basculer « aujourd'hui 00:30 » en « hier », et le test échouait selon l'heure du run.
    def jour(decalage: int, heure: int = 0, minute: int = 5) -> str:
        base = datetime.now() + timedelta(days=decalage)
        return base.replace(hour=heure, minute=minute, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )

    data = uh_schedule.load_registry()
    data["tasks"] = [
        {
            "id": "t-date-aujourdhui", "name": "aujourd'hui", "command": "doctor",
            "every_minutes": 15, "enabled": True, "created": jour(-1, 12),
            "last_run": jour(0), "last_status": "ok",
        },
        {
            "id": "t-date-hier", "name": "hier", "command": "doctor",
            "every_minutes": 60, "enabled": True, "created": jour(-2, 12),
            "last_run": jour(-1), "last_status": "ok",
        },
        {
            "id": "t-date-mois", "name": "mois", "command": "doctor",
            "every_minutes": 60, "enabled": True, "created": jour(-41, 12),
            "last_run": jour(-40, 12), "last_status": "ok",
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


def test_the_day_is_the_servers_day_not_the_browsers(
    browser, server_no_triggers, monkeypatch
) -> None:
    """Un passage d'aujourd'hui reste « Aujourd'hui » même consulté d'un autre fuseau.

    Le serveur écrit ses horodatages naïfs dans SON heure locale et publie désormais `now`.
    Comparés au calendrier du navigateur, ces mêmes horodatages basculaient en « Hier » dès
    que le poste était déjà un autre jour - le cas de toute consultation à distance. Le test
    place donc le navigateur dans un fuseau réellement décalé d'un jour (le navigateur doit
    être dans un autre jour que le serveur, et on le vérifie) : sans cette précaution, les
    deux calendriers coïncident et le défaut resterait invisible.
    """
    monkeypatch.setenv("UH_HOME", str(server_no_triggers.home))
    serveur = datetime.now()
    passage = serveur.replace(hour=0, minute=5, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    data = uh_schedule.load_registry()
    data["tasks"] = [
        {
            "id": "t-fuseau", "name": "fuseau", "command": "doctor", "every_minutes": 60,
            "enabled": True, "created": passage, "last_run": passage, "last_status": "ok",
        }
    ]
    uh_schedule.save_registry(data)

    # +14 tant que le serveur est assez avancé dans la journée, -11 sinon : dans les deux
    # cas, le navigateur est de l'autre côté de minuit par rapport au serveur.
    fuseau = "Pacific/Kiritimati" if serveur.hour >= 11 else "Pacific/Midway"

    context, page = open_app(browser, server_no_triggers, timezone_id=fuseau)
    try:
        jour_navigateur = page.evaluate("() => new Date().toLocaleDateString('sv-SE')")
        assert jour_navigateur != serveur.strftime("%Y-%m-%d"), (
            f"le navigateur ({fuseau}) devait être un autre jour que le serveur : "
            f"{jour_navigateur} vs {serveur:%Y-%m-%d}"
        )
        if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
            page.click("#providers-open")
        page.click("#group-tools")
        page.click("#tab-tasks")
        page.wait_for_function(
            "() => document.querySelectorAll('#tasks-list .task-row').length >= 1", timeout=20000
        )
        texte = page.eval_on_selector("#tasks-list", "el => el.innerText")
        assert "Aujourd'hui 00:05" in texte, (
            f"« Aujourd'hui » doit suivre l'horloge du serveur, pas celle du poste : {texte}"
        )
        assert "Hier 00:05" not in texte, f"un passage du jour affiché comme « Hier » : {texte}"
    finally:
        context.close()
