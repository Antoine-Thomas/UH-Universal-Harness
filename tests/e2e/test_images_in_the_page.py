"""Une image Markdown ne s'affiche jamais brute, et jamais en <img> distant.

Le cas de la capture de l'opérateur : la figure du cochon était déjà dessinée,
et « ![cochon](cochon.png) » restait écrit au-dessus. Le rendu passé ici est
celui de `markdown.js` dans la vraie page, avec les noms d'artefacts que
`app.js` lui transmet désormais.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

RENDER = """async (payload) => {
    const { renderMarkdown } = await import("/static/markdown.js");
    const probe = document.createElement("div");
    probe.id = "probe";
    probe.innerHTML = renderMarkdown(payload.text, { artifacts: payload.artifacts });
    document.body.append(probe);
}"""


def test_an_image_that_names_an_artifact_leaves_no_raw_text(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        page.evaluate(
            RENDER,
            {"text": "Voici le cochon.\n\n![cochon](cochon.png)", "artifacts": ["cochon.png"]},
        )
        probe = page.locator("#probe")
        expect(probe).to_have_text("Voici le cochon.")
        assert "![" not in probe.inner_text()
        assert page.locator("#probe img").count() == 0
    finally:
        context.close()


def test_an_external_image_is_a_link_not_an_image_tag(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        page.evaluate(
            RENDER,
            {"text": "![un chat](https://exemple.fr/chat.png)", "artifacts": []},
        )
        link = page.locator("#probe a")
        expect(link).to_have_count(1)
        expect(link).to_have_attribute("href", "https://exemple.fr/chat.png")
        expect(link).to_have_attribute("target", "_blank")
        # La politique de sécurité interdit les images distantes : aucun <img>
        # ne doit apparaître, même refusé par le navigateur.
        assert page.locator("#probe img").count() == 0
    finally:
        context.close()
