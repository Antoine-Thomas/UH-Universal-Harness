"""G8 phase 4, garde-fou 7 : l'en-tête CSP réel, et les deux thèmes en image.

Deux mesures, et une seule prétention : ce que le serveur envoie vraiment, et ce
que la carte donne à voir en clair et en sombre. L'en-tête est lu sur la réponse
de `/`, pas dans la constante du code — une constante peut mentir sur ce qui
part sur le réseau.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

CAPTURES = Path("uh-g8-captures")

RENDER = """async (links) => {
    const { linkCards } = await import("/static/links.js");
    const box = document.createElement("div");
    box.className = "msg assistant";
    box.id = "probe";
    document.getElementById("messages").append(box);
    linkCards(box, links, { session: "e2e" });
}"""

LINKS = [
    {"url": "https://exemple.test/un-article", "origin": "model"},
    {"url": "https://outil.test/resultat", "origin": "tool"},
]


def test_the_csp_header_is_the_one_we_believe(browser, server: Server) -> None:
    """`img-src 'self' data:` sur la réponse réelle : une image distante reste refusée."""
    _context, page = open_app(browser, server)
    response = page.goto(server.url, wait_until="domcontentloaded")
    assert response is not None
    csp = response.headers.get("content-security-policy", "")

    print(f"\nContent-Security-Policy réel : {csp}")
    assert csp, "la page doit porter un en-tête CSP"
    assert "img-src 'self' data:" in csp, f"CSP inattendue : {csp}"
    assert "script-src 'self'" in csp, f"CSP inattendue : {csp}"


def test_the_card_reads_the_same_in_both_themes(browser, server: Server) -> None:
    _context, page = open_app(browser, server)
    page.evaluate(RENDER, LINKS)
    expect(page.locator("#probe .link-card")).to_have_count(2)
    CAPTURES.mkdir(exist_ok=True)

    # La page démarre en sombre (`<html data-theme="dark">`) : régler le thème
    # explicitement pour chaque prise, sinon les deux captures sont identiques —
    # ce que la première version de ce test a produit sans s en apercevoir.
    page.locator("#probe").scroll_into_view_if_needed()
    page.evaluate("document.documentElement.dataset.theme = 'light'")
    page.wait_for_timeout(700)
    page.locator("#probe").screenshot(path=str(CAPTURES / "carte-liens-clair.png"))

    page.evaluate("document.documentElement.dataset.theme = 'dark'")
    page.wait_for_timeout(500)
    page.locator("#probe").screenshot(path=str(CAPTURES / "carte-liens-sombre.png"))

    for name in ("carte-liens-clair.png", "carte-liens-sombre.png"):
        size = (CAPTURES / name).stat().st_size
        print(f"{name} : {size} octets")
        assert size > 4000, f"{name} est vide ou presque ({size} octets)"
