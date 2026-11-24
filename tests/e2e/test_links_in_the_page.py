"""Une adresse écrite nue dans une réponse s'ouvre d'un clic, dans la vraie page.

Le rendu est celui de `markdown.js` tel que la page le charge — c'est le chemin de
`content.innerHTML = renderMarkdown(text)`, et les résultats de `/web` sont du
texte brut : sans autolien, une adresse y reste morte. Le clic est réellement
effectué, et son ouverture dans un nouvel onglet est constatée.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

RENDER = """async (text) => {
    const { renderMarkdown } = await import("/static/markdown.js");
    const probe = document.createElement("div");
    probe.id = "probe";
    probe.innerHTML = renderMarkdown(text);
    document.body.append(probe);
}"""


def test_a_bare_url_in_an_answer_opens_in_a_new_tab(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        target = f"{server.base}/une-page-qui-nexiste-pas"
        page.evaluate(RENDER, f"Résultat de la recherche : {target}.")

        link = page.locator("#probe a")
        expect(link).to_have_count(1)
        expect(link).to_have_attribute("href", target)
        expect(link).to_have_attribute("target", "_blank")
        expect(link).to_have_attribute("rel", "noopener noreferrer")
        # Le point de la phrase est resté dehors : c'est visible dans le texte.
        expect(page.locator("#probe")).to_have_text(f"Résultat de la recherche : {target}.")

        with context.expect_page() as popup:
            link.click()
        assert popup.value.url.endswith("/une-page-qui-nexiste-pas")
    finally:
        context.close()


def test_a_link_written_by_the_model_is_not_nested(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        page.evaluate(RENDER, "Voir [la page](https://exemple.fr/x) et https://exemple.fr/y")
        expect(page.locator("#probe a")).to_have_count(2)
        expect(page.locator("#probe a").first).to_have_attribute("href", "https://exemple.fr/x")
    finally:
        context.close()
