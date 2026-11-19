"""La modale d'attachement, prouvée par ce que l'écran calcule (P3-16b).

Ce fichier existe parce que les tests précédents cherchaient du **texte dans le source** :
ils ont dit « modale corrigée » pendant que l'écran affichait une fenêtre qui ne se fermait
pas. Ici, chaque contrôle lit `getComputedStyle` — l'état rendu, pas l'intention.

Mesure du 19/11 à l'origine du correctif : attribut `hidden` posé, `display: flex`,
620 px visibles. La règle `.plus-menu { display:flex }` écrasait `[hidden]` du navigateur.

    pytest -o addopts="" -m e2e tests/e2e/test_attach_modal.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402

MENU = "#plus-menu"


def display(page, selector: str) -> str:
    return page.eval_on_selector(selector, "el => getComputedStyle(el).display")


def test_the_modal_is_closed_when_the_page_opens(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        assert display(page, MENU) == "none", "une modale ne s'ouvre pas toute seule"
        assert display(page, "#plus-backdrop") == "none"
        assert "modal-open" not in (page.eval_on_selector("body", "el => el.className") or "")
    finally:
        context.close()


def test_it_opens_on_the_plus_and_covers_the_page(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        assert display(page, MENU) == "flex"
        assert display(page, "#plus-backdrop") != "none", "le fond assombri doit apparaître"
        assert "modal-open" in (page.eval_on_selector("body", "el => el.className") or "")
        box = page.eval_on_selector(MENU, "el => el.getBoundingClientRect().toJSON()")
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        assert box["width"] >= 560, f"la fenêtre doit être large, mesuré {box['width']}"
        assert box["width"] <= viewport["width"], "elle ne doit jamais déborder"
        assert abs((box["x"] + box["width"] / 2) - viewport["width"] / 2) < 40, "centrée"
    finally:
        context.close()


def test_it_closes_with_the_cross(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        page.click("#plus-close")
        assert display(page, MENU) == "none"
        assert display(page, "#plus-backdrop") == "none"
    finally:
        context.close()


def test_it_closes_with_escape(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        page.keyboard.press("Escape")
        assert display(page, MENU) == "none"
    finally:
        context.close()


def test_it_closes_on_a_click_outside(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        # un clic dans le fond, loin de la fenêtre
        page.mouse.click(8, 8)
        assert display(page, MENU) == "none"
    finally:
        context.close()


def test_the_filters_really_filter(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        cards = page.eval_on_selector_all(
            ".plus-item[data-kind]", "els => els.map(el => getComputedStyle(el).display)"
        )
        assert cards and all(value != "none" for value in cards), "« Tous » montre les quatre"

        page.click('.plus-filter[data-filter="image"]')
        visible = page.eval_on_selector_all(
            ".plus-item[data-kind]",
            "els => els.filter(el => getComputedStyle(el).display !== 'none')"
            ".map(el => el.dataset.kind)",
        )
        assert visible == ["image"], f"le filtre Images ne garde que les images : {visible}"

        page.click('.plus-filter[data-filter="document"]')
        visible = page.eval_on_selector_all(
            ".plus-item[data-kind]",
            "els => els.filter(el => getComputedStyle(el).display !== 'none')"
            ".map(el => el.dataset.kind)",
        )
        assert visible == ["document"], f"le filtre Documents : {visible}"
    finally:
        context.close()


def test_it_closes_itself_once_files_are_attached(browser, server, tmp_path: Path) -> None:
    """Le geste qui restait bloqué : on choisit un fichier, la fenêtre s'efface."""
    picture = tmp_path / "capture.png"
    picture.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
        b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
        b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        page.set_input_files("#files", str(picture))
        page.wait_for_timeout(1200)  # l'import est asynchrone
        assert display(page, MENU) == "none", "la fenêtre doit se fermer dès l'import"
    finally:
        context.close()


def test_the_static_build_token_is_the_commit(server) -> None:
    """`?v=` doit changer quand le code change, pas quand la version change."""
    import httpx

    page = httpx.get(server.url, timeout=10).text
    assert "app.js?v=" in page and "style.css?v=" in page
    for token in ("app.js?v=", "style.css?v="):
        value = page.split(token, 1)[1].split('"', 1)[0]
        assert value and value != "1.9.0", f"jeton de cache encore lié à la version : {value}"
