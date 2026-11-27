"""P1-8 à l'écran : le badge du second cerveau distingue absent, erreur et ok.

Le serveur décide (`state`), la page affiche. Ce test vérifie les trois libellés,
la classe qui les colore, et le fait qu'un clic sur « absent » ouvre le panneau qui
peut le démarrer au lieu de tenter un changement de mode sans objet.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402

OK = {
    "connected": True,
    "state": "ok",
    "sentence": "Second cerveau joignable.",
    "mode": "chat",
    "version": "1.9.2",
}
ABSENT = {
    "connected": False,
    "state": "absent",
    "sentence": "Second cerveau absent : rien ne répond à cette adresse.",
}
ERROR = {
    "connected": False,
    "state": "error",
    "sentence": "Second cerveau en erreur : uhb returned invalid JSON",
}


def mock_status(page, payload: dict) -> None:
    """Pose la réponse **avant** le chargement : c'est `boot()` qui lit l'état.

    `refreshModeBadge` n'est pas une globale (le script est un module), donc un
    `page.evaluate` ne peut pas l'appeler : on rejoue le démarrage, comme un
    utilisateur qui ouvre la page.
    """
    page.route("**/api/uhb/status", lambda route: route.fulfill(status=200, json=payload))
    page.reload(wait_until="domcontentloaded")
    page.wait_for_selector("#mode-badge", timeout=30000)
    page.wait_for_timeout(2000)


def badge(page):
    return page.locator("#mode-badge")


def test_an_absent_uhb_is_named_absent(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_status(page, ABSENT)

        assert "absent" in badge(page).inner_text()
        assert badge(page).get_attribute("data-uhb") == "absent"
        assert "absent" in (badge(page).get_attribute("class") or "")
        assert "rien ne répond" in (badge(page).get_attribute("title") or "")
    finally:
        context.close()


def test_a_broken_uhb_says_error_not_offline(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_status(page, ERROR)

        assert "en erreur" in badge(page).inner_text()
        assert badge(page).get_attribute("data-uhb") == "error"
        assert "error" in (badge(page).get_attribute("class") or "")
        assert "journal" in (badge(page).get_attribute("title") or "")
    finally:
        context.close()


def test_a_healthy_uhb_shows_its_mode(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_status(page, OK)

        text = badge(page).inner_text()
        assert "absent" not in text and "erreur" not in text
        assert badge(page).get_attribute("data-uhb") == "chat"
        assert "lecture seule" in text
    finally:
        context.close()


def test_clicking_absent_opens_the_panel_instead_of_switching_mode(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_status(page, ABSENT)

        badge(page).click()
        page.wait_for_timeout(400)

        assert page.eval_on_selector("#uhb-view", "el => el.hidden === false"), (
            "le panneau UHB doit s'ouvrir pour proposer le démarrage"
        )
    finally:
        context.close()


def test_the_panel_repeats_the_servers_sentence(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_status(page, ERROR)
        page.click("#group-tools")
        page.wait_for_timeout(150)
        page.click("#tab-uhb")
        page.wait_for_timeout(600)

        panel = page.locator("#uhb-panel")
        assert "Second cerveau en erreur" in panel.inner_text()
        assert panel.locator("[data-uhb-state='error']").count() == 1
    finally:
        context.close()
