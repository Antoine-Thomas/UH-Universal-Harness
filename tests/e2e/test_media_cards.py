"""Les cartes : ce qu'elles affichent, et à qui elles parlent.

Le point central : une galerie affiche des vignettes **sans jamais demander
l'image à un tiers**. Le navigateur ne connaît que la route de UH ; c'est elle
qui va chercher les octets (et qui peut refuser). L'interception réseau le
prouve, requête par requête.
"""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

THIRD_PARTIES = ("api.openverse.org", "thumb.wikimedia.org", "upload.wikimedia.org", "i.ytimg.com")

RENDER = """async (payload) => {
    const { drawCards } = await import("/static/cards.js");
    const box = document.createElement("div");
    box.id = "probe";
    box.className = "msg assistant";
    document.getElementById("messages").append(box);
    drawCards(box, payload, { session: "e2e" });
}"""

OPENVERSE_THUMB = "https://api.openverse.org/v1/images/4a8aa2a9/thumb/"
GALLERY = {
    "kind": "images",
    "query": "cochon",
    "results": [
        {
            "title": "le cochon",
            "thumb": OPENVERSE_THUMB,
            "page": "https://www.flickr.com/photos/30533017@N00/16250817413",
            "domain": "www.flickr.com",
            "licence": "by 2.0",
            "author": "pilllpat (agence eureka)",
            "provider": "flickr",
        }
    ],
    "failed": {},
}


def test_a_gallery_shows_licence_author_and_source(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        page.evaluate(RENDER, GALLERY)

        gallery = page.locator("#probe .gallery")
        expect(gallery).to_have_count(1)
        expect(gallery.locator("figure")).to_have_count(1)
        expect(gallery.locator(".card-title")).to_have_text("le cochon")
        expect(gallery.locator(".card-licence")).to_have_text(
            "licence : by 2.0 — auteur : pilllpat (agence eureka)"
        )
        source = gallery.locator("a.card-source")
        expect(source).to_have_attribute("href", GALLERY["results"][0]["page"])
        expect(source).to_have_attribute("target", "_blank")
        expect(source).to_have_attribute("rel", "noopener noreferrer")
        expect(gallery.locator(".card-domain")).to_have_text("www.flickr.com")
        expect(gallery.locator(".card-provider")).to_have_text("flickr")
    finally:
        context.close()


def test_the_thumbnail_is_asked_to_uh_and_never_to_the_third_party(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    seen: list[str] = []
    answers: list[tuple[str, int]] = []
    page.on("request", lambda request: seen.append(request.url))
    page.on("response", lambda response: answers.append((response.url, response.status)))
    try:
        page.evaluate(RENDER, GALLERY)
        # L'image est demandée tout de suite : on attend que la route réponde.
        page.wait_for_function(
            "() => performance.getEntriesByType('resource')"
            ".some((entry) => entry.name.includes('/api/media/thumb'))",
            timeout=20000,
        )

        thumbs = [url for url in seen if "/api/media/thumb" in url]
        assert thumbs, "la vignette doit passer par la route de UH"
        assert OPENVERSE_THUMB in unquote(thumbs[0]), "l'URL distante est un paramètre"
        # La route porte l'adresse distante dans sa requête : elle est de UH, donc
        # on l'écarte avant de chercher une requête partie chez un tiers.
        elsewhere = [url for url in seen if "/api/media/thumb" not in url]
        for host in THIRD_PARTIES:
            assert not [url for url in elsewhere if host in url], (
                f"aucune requête directe ne doit partir vers {host}"
            )
        # Tout ce qui est parti est chez UH, et rien d'autre.
        assert all(url.startswith(server.base) or url.startswith("data:") for url in seen), seen
        status = [code for url, code in answers if "/api/media/thumb" in url]
        assert status and status[0] in (200, 400), status
    finally:
        context.close()


def test_a_host_outside_the_list_fails_visibly_without_a_request(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    seen: list[str] = []
    answers: list[tuple[str, int]] = []
    page.on("request", lambda request: seen.append(request.url))
    page.on("response", lambda response: answers.append((response.url, response.status)))
    try:
        payload = {
            "kind": "images",
            "query": "cochon",
            "results": [
                {
                    "title": "hors liste",
                    "thumb": "https://exemple.fr/x.png",
                    "page": "https://exemple.fr/page",
                    "domain": "exemple.fr",
                    "licence": "",
                    "author": "",
                    "provider": "",
                }
            ],
            "failed": {},
        }
        page.evaluate(RENDER, payload)

        figure = page.locator("#probe figure")
        expect(figure).to_have_class("gallery-item unavailable", timeout=20000)
        expect(figure.locator(".card-licence")).to_have_text(
            "licence : inconnue — auteur : inconnu"
        )
        elsewhere = [url for url in seen if "/api/media/thumb" not in url]
        assert not [url for url in elsewhere if "exemple.fr" in url]
        refused = [code for url, code in answers if "/api/media/thumb" in url]
        assert refused and refused[0] == 400, refused
    finally:
        context.close()


def test_web_results_are_cards_with_a_clickable_title(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        page.evaluate(
            RENDER,
            {
                "kind": "web",
                "query": "cochon",
                "engine": "duckduckgo",
                "results": [
                    {
                        "title": "Un article",
                        "url": "https://exemple.fr/article",
                        "domain": "exemple.fr",
                        "snippet": "Un extrait lisible.",
                    }
                ],
                "failed": {"brave": "HTTP 429"},
            },
        )

        cards = page.locator("#probe .web-card")
        expect(cards).to_have_count(1)
        title = cards.locator("a.card-title")
        expect(title).to_have_text("Un article")
        expect(title).to_have_attribute("href", "https://exemple.fr/article")
        expect(title).to_have_attribute("target", "_blank")
        expect(title).to_have_attribute("rel", "noopener noreferrer")
        expect(cards.locator(".card-domain")).to_have_text("exemple.fr")
        expect(cards.locator(".card-snippet")).to_have_text("Un extrait lisible.")
        # Un moteur en échec est nommé, pas enterré.
        notes = page.locator("#probe .card-note")
        expect(notes.nth(1)).to_contain_text("brave")
        expect(page.locator("#probe img")).to_have_count(0)
    finally:
        context.close()


def test_a_video_card_has_no_iframe_and_says_what_it_lacks(browser, server: Server) -> None:
    context, page = open_app(browser, server)
    try:
        page.evaluate(
            RENDER,
            {
                "kind": "video",
                "title": "Me at the zoo",
                "channel": "jawed",
                "channel_url": "https://www.youtube.com/@jawed",
                "thumb": "https://i.ytimg.com/vi/jNQXAC9IVRw/hqdefault.jpg",
                "page": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
                "description": "",
                "has_description": False,
            },
        )

        card = page.locator("#probe .video-card")
        expect(card).to_have_count(1)
        expect(page.locator("#probe iframe")).to_have_count(0), "aucun lecteur intégré"
        expect(card.locator("a.card-title")).to_have_text("Me at the zoo")
        expect(card.locator(".card-meta").first).to_have_text("jawed")
        expect(card.locator(".card-note")).to_contain_text("description indisponible")
        open_link = card.locator("a.notice-action")
        expect(open_link).to_have_text("Ouvrir sur YouTube")
        expect(open_link).to_have_attribute("href", "https://www.youtube.com/watch?v=jNQXAC9IVRw")
        expect(open_link).to_have_attribute("rel", "noopener noreferrer")
    finally:
        context.close()
