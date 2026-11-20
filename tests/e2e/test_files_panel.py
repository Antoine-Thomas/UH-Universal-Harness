"""L'explorateur de fichiers, prouvé à l'écran (P3-16c lot 1).

Les tests serveur (`tests/test_web_fs.py`) prouvent la borne ; celui-ci prouve que
l'interface la respecte et fait ce qu'elle annonce : fil d'Ariane, arborescence, aperçu
d'une image et bouton « Attacher au tour » qui ajoute vraiment une pièce jointe.

    pytest -o addopts="" -m e2e tests/e2e/test_files_panel.py -q
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402

PNG_IN_REPO = r"docs\assets\cockpit.png"


def open_files(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.click("#group-projects")
    page.click("#tab-files")
    page.wait_for_selector("#files-list .files-row", timeout=20000)


def test_the_explorer_opens_on_the_workspace_with_a_breadcrumb(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_files(page)
        crumbs = page.eval_on_selector_all(
            "#files-crumbs .files-crumb", "els => els.map(el => el.textContent.trim())"
        )
        assert crumbs and crumbs[0] == "workspace", f"fil d'Ariane : {crumbs}"
        hint = page.eval_on_selector("#files-hint", "el => el.textContent")
        assert "élément" in hint, f"le listing annonce son compte : {hint!r}"
        names = page.eval_on_selector_all(
            "#files-list .files-name", "els => els.map(el => el.textContent.trim())"
        )
        assert any("README.md" in name for name in names), f"le dépôt est listé : {names[:6]}"
    finally:
        context.close()


def test_a_folder_opens_and_the_crumb_walks_back(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_files(page)
        page.click("#files-list .files-name[data-kind='dir']")
        page.wait_for_timeout(1200)
        crumbs = page.eval_on_selector_all(
            "#files-crumbs .files-crumb", "els => els.map(el => el.textContent.trim())"
        )
        assert len(crumbs) >= 2, f"on est entré dans un dossier : {crumbs}"
        page.click("#files-crumbs .files-crumb")  # retour au workspace
        page.wait_for_timeout(1200)
        crumbs = page.eval_on_selector_all(
            "#files-crumbs .files-crumb", "els => els.map(el => el.textContent.trim())"
        )
        assert crumbs == ["workspace"], f"le fil d'Ariane ramène : {crumbs}"
    finally:
        context.close()


def test_an_image_preview_is_actually_rendered(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_files(page)
        page.click("#files-crumbs .files-crumb")  # workspace
        page.wait_for_timeout(800)
        # on descend dans docs/assets par le fil, sans deviner de séparateur
        page.click("#files-list .files-name:has-text('docs')")
        page.wait_for_timeout(1000)
        page.click("#files-list .files-name:has-text('assets')")
        page.wait_for_timeout(1000)
        page.click("#files-list .files-name:has-text('cockpit.png')")
        page.wait_for_selector("#files-preview-body img.files-image", timeout=20000)
        img = page.eval_on_selector(
            "#files-preview-body img.files-image",
            """el => ({ src: el.getAttribute('src').slice(0, 30),
                        width: el.naturalWidth, height: el.naturalHeight })""",
        )
        assert img["src"].startswith("data:image/png;base64,"), img
        assert img["width"] > 0 and img["height"] > 0, f"l'image n'a pas de pixels : {img}"
        name = page.eval_on_selector("#files-preview-name", "el => el.textContent")
        assert "cockpit.png" in name
    finally:
        context.close()


def test_attach_adds_a_real_attachment(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_files(page)
        page.click("#files-list .files-name:has-text('README.md')")
        page.wait_for_selector("#files-preview-body pre.files-text", timeout=20000)
        page.click("#files-attach")
        page.wait_for_selector("#attachments .attachment", timeout=20000)
        pastille = page.eval_on_selector_all(
            "#attachments .attachment", "els => els.map(el => el.textContent)"
        )
        assert any("README.md" in text for text in pastille), f"pièce jointe absente : {pastille}"
        hint = page.eval_on_selector("#files-hint", "el => el.textContent")
        assert "joint au prochain tour" in hint, f"le geste est confirmé : {hint!r}"
    finally:
        context.close()


def test_the_explorer_never_opens_the_configuration_folder(browser, server) -> None:
    """Le refus est celui du serveur : l'interface l'affiche au lieu de le masquer."""
    context, page = open_app(browser, server)
    try:
        open_files(page)
        result = page.evaluate(
            """async () => {
                const response = await fetch('/api/fs/preview?path=' + encodeURIComponent('~/.uh/keys.toml'),
                    { credentials: 'same-origin' });
                const body = await response.json().catch(() => ({}));
                return { status: response.status, detail: String(body.detail || '') };
            }"""
        )
        assert result["status"] == 403, result
        assert "refused" in result["detail"], result
    finally:
        context.close()
