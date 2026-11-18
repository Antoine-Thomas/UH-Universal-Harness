"""P3-14 in a real browser: choose a character, keep it, change it, and keep that.

What the operator asked for is a place to set the posture *and* to switch it whenever
the project changes. The tests walk that path with real clicks:

* the header selector applies a preset, and a reload restores it from SQLite;
* the panel lists every preset plus free text, and marks the active one;
* a mid-conversation change replaces the role without touching anything else;
* free text is accepted and kept across a reload.

`uh web` serves a strict CSP (`script-src 'self'`), so `page.wait_for_function`
cannot be used — it evaluates a string and the policy refuses it. Every wait here is
a locator expectation, which polls inside the driver and works under that policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import expect

from .conftest import open_app

pytestmark = pytest.mark.e2e

PRESET_LABELS = [
    "Architecte logiciel senior",
    "Développeur full-stack",
    "Analyste de données",
    "Rédacteur produit concis",
    "Auditeur sécurité",
    "Domotique / Home Assistant",
    "Vision / vidéo IA",
]


def with_panel(server, panel: str) -> str:
    return f"{server.url}{'&' if '?' in server.url else '?'}panel={panel}"


def test_a_preset_chosen_in_the_header_survives_a_reload(
    make_server, browser, tmp_path: Path
) -> None:
    server = make_server("home")
    context, page = open_app(browser, server)
    try:
        expect(page.locator("#role-select option")).to_have_count(8, timeout=20000)
        page.locator("#role-select").select_option("analyste")
        expect(page.locator("#role-select")).to_have_value("analyste", timeout=15000)
        expect(page.locator("#role-select option:checked")).to_have_text("Analyste de données")

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        expect(page.locator("#role-select option")).to_have_count(8, timeout=20000)
        expect(page.locator("#role-select")).to_have_value("analyste", timeout=20000)

        # Mid-conversation change: the second choice replaces the first on the same
        # session, and the reload keeps the latest one.
        page.locator("#role-select").select_option("vision")
        expect(page.locator("#role-select")).to_have_value("vision", timeout=15000)
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        expect(page.locator("#role-select option")).to_have_count(8, timeout=20000)
        expect(page.locator("#role-select")).to_have_value("vision", timeout=20000)
        expect(page.locator("#role-select option:checked")).to_have_text("Vision / vidéo IA")
    finally:
        context.close()


def test_the_panel_lists_every_preset_plus_free_text(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context = browser.new_context()
    page = context.new_page()
    page.goto(with_panel(server, "role"), wait_until="domcontentloaded")
    try:
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        labels = page.locator("#role-panel .role-option span")
        expect(labels).to_have_count(9, timeout=20000)
        expect(labels).to_have_text(["Aucun rôle", *PRESET_LABELS, "Texte libre"])

        page.click("#role-panel .role-option[data-role='securite']")
        expect(page.locator("#role-select")).to_have_value("securite", timeout=15000)
        expect(page.locator("#role-panel .role-option.active")).to_have_attribute(
            "data-role", "securite", timeout=15000
        )
    finally:
        context.close()


def test_a_free_text_role_is_kept_and_shown_in_the_header(
    make_server, browser, tmp_path: Path
) -> None:
    server = make_server("home")
    context = browser.new_context()
    page = context.new_page()
    page.goto(with_panel(server, "role"), wait_until="domcontentloaded")
    try:
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        page.click("#role-panel .role-option[data-role='custom']")
        page.fill("#role-label", "Architecte de la maison")
        page.fill("#role-posture", "Réponds comme un architecte du bâtiment : surfaces, budget.")
        page.click("#role-custom-save")
        expect(page.locator("#role-select")).to_have_value("custom", timeout=15000)
        expect(page.locator("#role-select option:checked")).to_have_text("Architecte de la maison")

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        # The 8 preset options plus the free-text one the session now carries.
        expect(page.locator("#role-select option")).to_have_count(9, timeout=20000)
        expect(page.locator("#role-select")).to_have_value("custom", timeout=20000)
        expect(page.locator("#role-select option:checked")).to_have_text(
            "Architecte de la maison"
        ), "le libelle libre revient apres rechargement"
    finally:
        context.close()
