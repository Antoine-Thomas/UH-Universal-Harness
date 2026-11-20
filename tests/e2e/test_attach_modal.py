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


def test_the_cards_keep_the_border_the_modal_gives_them(browser, server) -> None:
    """Le bloc de l'ancien popover posait `border:0` sur `.plus-item` : les quatre
    familles s'affichaient sans cadre, dans une modale aux cartes neutres."""
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        borders = page.eval_on_selector_all(
            ".plus-item[data-kind]",
            "els => els.map(el => getComputedStyle(el).borderTopWidth)",
        )
        assert borders and all(value != "0px" for value in borders), f"cadres retirés : {borders}"
    finally:
        context.close()


def test_the_veil_really_dims_the_page(browser, server) -> None:
    """Le voile existait mais restait lisible : on lit l'alpha calculé, pas la règle."""
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        veil = page.eval_on_selector(
            "#plus-backdrop",
            """el => {
                const style = getComputedStyle(el);
                const match = style.backgroundColor.match(/rgba?\\(([^)]+)\\)/);
                const parts = match ? match[1].split(",").map(v => parseFloat(v)) : [];
                const rect = el.getBoundingClientRect();
                return {
                    alpha: parts.length === 4 ? parts[3] : 1,
                    width: Math.round(rect.width),
                    height: Math.round(rect.height),
                    viewport: [window.innerWidth, window.innerHeight],
                };
            }""",
        )
        assert veil["alpha"] >= 0.6, f"le fond doit assombrir, alpha mesuré {veil['alpha']}"
        assert veil["width"] >= veil["viewport"][0] and veil["height"] >= veil["viewport"][1]
    finally:
        context.close()


def test_a_fast_reopen_still_closes_on_the_backdrop(browser, server) -> None:
    """Fermer, rouvrir aussitôt, puis cliquer le fond : la modale doit repartir.
    Le cas n'était pas couvert - et c'est celui du double-clic sur +."""
    context, page = open_app(browser, server)
    try:
        for _ in range(2):
            page.click("#attach")
            page.click("#plus-close")
        page.click("#attach")
        assert display(page, MENU) == "flex"
        page.mouse.click(8, 8)
        assert display(page, MENU) == "none", "un fond cliqué après réouverture ferme la modale"
        assert display(page, "#plus-backdrop") == "none"
    finally:
        context.close()


def test_an_empty_filter_says_so(browser, server) -> None:
    """Une grille vide se lisait comme une panne de chargement ; elle s'annonce."""
    context, page = open_app(browser, server)
    try:
        page.click("#attach")
        assert display(page, "#plus-empty") == "none", "un filtre plein ne dit rien"

        # Un filtre qui ne garde plus rien : on retire la dernière carte Images.
        page.eval_on_selector(
            '.plus-item[data-kind="image"]',
            "el => el.remove()",
        )
        page.click('.plus-filter[data-filter="image"]')
        assert display(page, "#plus-empty") != "none"
        text = page.eval_on_selector("#plus-empty", "el => el.textContent.trim()")
        assert "Aucune image" in text, f"le message doit nommer le filtre : {text!r}"

        page.click('.plus-filter[data-filter="document"]')
        assert display(page, "#plus-empty") == "none", "un autre filtre reste plein"
    finally:
        context.close()


def test_the_keyboard_stays_in_the_modal_and_comes_back_to_plus(browser, server) -> None:
    """Piège de tabulation, puis retour du focus sur `+` : sans cela la tabulation
    repartait du haut de la page après chaque fermeture."""
    context, page = open_app(browser, server)
    try:
        page.focus("#attach")
        page.keyboard.press("Enter")
        assert display(page, MENU) == "flex"
        assert page.evaluate("document.activeElement.closest('#plus-menu') !== null")

        inside = []
        for _ in range(14):
            page.keyboard.press("Tab")
            inside.append(page.evaluate("document.activeElement.closest('#plus-menu') !== null"))
        assert all(inside), f"la tabulation est sortie de la modale : {inside}"

        page.keyboard.press("Shift+Tab")
        assert page.evaluate("document.activeElement.closest('#plus-menu') !== null")

        page.keyboard.press("Escape")
        assert display(page, MENU) == "none"
        assert page.evaluate("document.activeElement.id") == "attach", "le focus revient sur +"
    finally:
        context.close()
