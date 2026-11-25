"""Pas 3 : la carte de lien sur un VRAI tour, avec le réseau compté.

Rien n'est monté par import direct ici : le faux fournisseur répond un texte qui
contient deux URL, le serveur classe leur provenance et la trame `answer` porte
`links`, la page dessine la carte. Ce qui est mesuré, requête par requête :

* une URL écrite par le modèle ne déclenche **aucune** requête d'aperçu avant le
  clic — la carte affiche le domaine et un bouton, un point c'est tout ;
* le clic déclenche exactement une requête, vers `/api/link/preview` de UH, et
  jamais vers le site tiers ;
* l'interrupteur étant coupé par défaut, la carte propose de l'activer.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import open_app
from .conftest_fake_provider import fake_provider  # noqa: F401 - fixture importée

pytestmark = pytest.mark.e2e


def preview_calls(seen: list) -> list:
    return [url for url in seen if "/api/link/preview" in url]


def third_party(seen: list) -> list:
    hosts = ("modele.test", "outil.test")
    return [
        url
        for url in seen
        if any(host in url for host in hosts)
        and not url.split("?", 1)[0].rstrip("/").endswith(("/api/link/preview", "/api/link/image"))
    ]


def test_a_real_turn_shows_the_card_without_asking_anything(browser, fake_provider) -> None:
    server, fake = fake_provider
    _context, page = open_app(browser, server)
    seen: list = []
    page.on("request", lambda request: seen.append(request.url))

    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#model-select option'))
                 .some(o => (o.value + ' ' + o.textContent).toLowerCase().includes('fake'))""",
        timeout=20000,
    )
    value = page.evaluate(
        """() => Array.from(document.querySelectorAll('#model-select option'))
             .find(o => (o.value + ' ' + o.textContent).toLowerCase().includes('fake')).value"""
    )
    page.select_option("#model-select", value)
    page.wait_for_timeout(2000)

    page.fill("#input", "montre-moi une page")
    page.press("#input", "Enter")

    expect(page.locator(".link-card").first).to_be_visible(timeout=30000)
    assert fake.request_log(), "le tour doit passer par le faux fournisseur"
    assert page.locator(".link-card").count() >= 1
    page.wait_for_timeout(800)
    assert preview_calls(seen) == [], "aucune requête d'aperçu avant le clic"
    assert third_party(seen) == [], "aucune requête vers le site tiers"

    page.locator(".link-card-preview").first.click()
    page.wait_for_timeout(1200)
    assert len(preview_calls(seen)) == 1, "le clic déclenche exactement un aperçu"
    assert third_party(seen) == []
