"""Pas 2 : un vrai `uh web` dont le fournisseur est le faux.

Ce qui est mesuré ici, et rien de plus : la page se charge, un tour de chat part,
et c'est le faux serveur qui répond — donc la sélection du fournisseur a bien pris
depuis le `UH_HOME` temporaire, avant le démarrage.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import open_app

pytestmark = pytest.mark.e2e


def test_the_page_loads_and_the_fake_provider_answers(browser, fake_provider) -> None:
    server, fake = fake_provider

    _context, page = open_app(browser, server)
    # La liste du haut se remplit après /api/models : on attend l'option, on ne
    # devine pas un délai. C'est le chemin de l'opérateur, donc la bonne session.
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#model-select option'))
                 .some(o => (o.value + ' ' + o.textContent).toLowerCase().includes('fake'))""",
        timeout=20000,
    )
    value = page.evaluate(
        """() => Array.from(document.querySelectorAll('#model-select option'))
             .find(o => (o.value + ' ' + o.textContent).toLowerCase().includes('fake')).value"""
    )
    print("option choisie :", value)
    page.select_option("#model-select", value)
    page.wait_for_timeout(2000)
    page.fill("#input", "bonjour")
    page.press("#input", "Enter")

    try:
        expect(page.locator(".msg.assistant").last).to_contain_text("outil.test", timeout=12000)
    except AssertionError:
        # Une seule exécution qui imprime tout : la requête est-elle arrivée, et
        # que dit le serveur quand elle n'aboutit pas ?
        print("\n--- faux serveur, requêtes reçues ---", fake.request_log())
        print("--- 25 dernières lignes de uh web ---")
        print("\n".join(server.lines[-25:]))
        print("--- bulle ---", repr(page.locator(".msg.assistant").last.inner_text()[:200]))
        raise
    assert fake.request_log(), "le tour doit passer par le faux fournisseur"
