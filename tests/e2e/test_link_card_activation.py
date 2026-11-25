"""G8 : activer les aperçus depuis la carte, et seulement par un clic.

Le réglage est coupé par défaut — c'est le bon défaut, parce qu'un aperçu est une
requête vers un site tiers. Mais renvoyer « activez les aperçus dans le panneau
Outils » oblige à chercher. La carte porte donc le bouton.

Ce que ce fichier mesure :

1. sans clic, le réglage reste coupé (aucune activation au chargement) ;
2. le clic active le réglage (POST /api/link/settings) et la carte se rafraîchit ;
3. l'état d'origine est restauré en sortie, quel que soit le résultat — un test ne
   laisse pas les aperçus allumés sur la machine de l'opérateur.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

RENDER = """async (links) => {
    const { linkCards } = await import("/static/links.js");
    const box = document.createElement("div");
    box.className = "msg assistant";
    box.id = "probe";
    document.getElementById("messages").append(box);
    linkCards(box, links, { session: "e2e" });
}"""

MODEL_LINK = [{"url": "https://exemple.test/page", "origin": "model"}]

READ = """async () => (await fetch("/api/link/settings", { credentials: "same-origin" })).json()"""

WRITE = """async (enabled) => (await fetch("/api/link/settings", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
})).json()"""


def test_no_click_means_no_setting_change(browser, server: Server) -> None:
    _context, page = open_app(browser, server)
    original = page.evaluate(READ)
    try:
        # On part d'un état connu : coupé.
        page.evaluate(WRITE, False)
        page.evaluate(RENDER, MODEL_LINK)
        page.locator("#probe .link-card-preview").click()
        expect(page.locator("#probe .link-card-error")).to_be_visible()
        # Le bouton est proposé, mais rien n'est activé tant qu'il n'est pas cliqué.
        expect(page.locator("#probe .link-card-activate")).to_be_visible()
        assert page.evaluate(READ)["enabled"] is False, "aucune activation sans clic"

        # Le clic vaut consentement : le réglage passe à actif, la carte se rafraîchit.
        page.locator("#probe .link-card-activate").click()
        page.wait_for_timeout(1200)
        assert page.evaluate(READ)["enabled"] is True, "le clic active le réglage"
        # La carte se rafraîchit : l'erreur « désactivés » disparaît et le bouton
        # d'activation avec elle. L'aperçu suivant peut échouer pour une autre
        # raison (ici, un domaine de test injoignable) — c'est une autre erreur.
        assert page.locator("#probe .link-card-activate").count() == 0, "le bouton disparaît"
        body = page.locator("#probe .link-card").inner_text()
        assert "désactiv" not in body, f"la carte se rafraîchit : {body[:120]}"
    finally:
        page.evaluate(WRITE, bool(original.get("enabled")))
