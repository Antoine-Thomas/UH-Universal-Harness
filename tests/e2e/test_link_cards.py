"""Les cartes de lien : quand la requête part, et quand elle ne part pas.

Le point central de G8 : une URL écrite par le modèle n'est **pas** récupérée
sans un clic. Ce fichier le prouve en comptant les requêtes du navigateur, pas en
lisant le code : c'est le réseau qui parle.

Trois mesures :

1. provenance ``model`` → zéro requête d'aperçu avant le clic, une après ;
2. provenance ``tool`` → l'aperçu part tout seul (le serveur re-vérifie la
   provenance déclarée, donc la carte n'a rien à décider) ;
3. dans les deux cas, **aucune** requête ne va vers le site tiers : tout passe
   par les routes de UH.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

RENDER = """async (links) => {
    const { linkCards } = await import("/static/links.js");
    const box = document.createElement("div");
    box.id = "probe";
    box.className = "msg assistant";
    document.getElementById("messages").append(box);
    linkCards(box, links, { session: "e2e" });
}"""

MODEL_LINK = [{"url": "https://exemple.test/page", "origin": "model"}]
TOOL_LINK = [{"url": "https://outil.test/page", "origin": "tool"}]


def watch(page) -> list:
    seen: list = []
    page.on("request", lambda request: seen.append(request.url))
    return seen


def preview_calls(seen: list) -> list:
    return [url for url in seen if "/api/link/preview" in url]


def third_party_calls(seen: list) -> list:
    """Les requêtes qui visent vraiment le tiers, pas la route de UH qui le nomme.

    /api/link/preview?url=… porte l'adresse dans sa requête : la compter comme
    un appel au tiers serait un faux positif, et c'est exactement ce que la
    première version de ce test a fait.
    """
    third = ("exemple.test", "outil.test")
    return [
        url
        for url in seen
        if any(host in url for host in third)
        and not url.split("?", 1)[0].rstrip("/").endswith(("/api/link/preview", "/api/link/image"))
    ]


def test_a_model_url_asks_nothing_until_the_click(browser, server: Server) -> None:
    _context, page = open_app(browser, server)
    seen = watch(page)

    page.evaluate(RENDER, MODEL_LINK)
    card = page.locator("#probe .link-card")
    expect(card).to_be_visible()
    # Le domaine s'affiche sans rien demander : c'est le seul contenu gratuit.
    expect(page.locator("#probe .link-card-domain")).to_have_text("exemple.test")
    page.wait_for_timeout(600)
    assert preview_calls(seen) == [], "aucune requête d'aperçu avant le clic"

    page.locator("#probe .link-card-preview").click()
    page.wait_for_timeout(900)
    assert len(preview_calls(seen)) == 1, "le clic déclenche exactement un aperçu"
    assert third_party_calls(seen) == [], "le site tiers n'est jamais appelé par la page"


def test_a_tool_url_is_previewed_without_a_click(browser, server: Server) -> None:
    _context, page = open_app(browser, server)
    seen = watch(page)

    page.evaluate(RENDER, TOOL_LINK)
    page.wait_for_timeout(900)

    assert len(preview_calls(seen)) >= 1, "une URL de résultat d'outil est récupérée"
    assert third_party_calls(seen) == [], "toujours aucune requête vers le tiers"
    expect(page.locator("#probe .link-card-preview")).to_have_count(0)


def test_the_card_has_no_html_injected(browser, server: Server) -> None:
    """Titre et description arrivent en texte : aucun élément fabriqué.

    Le serveur ne renvoie que des chaînes ; la carte les pose par `textContent`.
    Un `<script>` dans le domaine reste donc du texte, et aucune balise n'apparaît
    dans la carte en dehors de celles du module.
    """
    _context, page = open_app(browser, server)

    page.evaluate(RENDER, [{"url": "https://exemple.test/<script>x</script>", "origin": "model"}])
    card = page.locator("#probe .link-card")
    expect(card).to_be_visible()

    assert page.evaluate("document.querySelectorAll('#probe script').length") == 0
    # textContent échappe : le texte est là, la balise n'existe pas.
    html = page.evaluate("document.querySelector('#probe').innerHTML")
    assert "&lt;script&gt;" in html
