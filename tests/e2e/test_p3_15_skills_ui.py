"""Lot 2 — la gestion des Skills dans le vrai navigateur.

Ce que l'operateur doit pouvoir faire sans redemarrer UH : voir ses Skills, cocher,
decocher, tout activer ou tout desactiver, et retrouver son choix apres un
rafraichissement. La case ne donne aucune permission : elle decide seulement si le
Skill peut servir de contexte au tour.

`uh web` sert `script-src 'self'` : les attentes sont des `expect(locator)`, jamais
`page.wait_for_function` (qui evalue une chaine et se fait refuser par la CSP).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def tools_panel(server) -> str:
    return f"{server.url}{'&' if '?' in server.url else '?'}panel=tools"


def test_the_skills_list_shows_origin_and_state(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context = browser.new_context()
    page = context.new_page()
    page.goto(tools_panel(server), wait_until="domcontentloaded")
    try:
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        rows = page.locator("#skills-manager .skill-row")
        expect(rows.first).to_be_visible(timeout=20000)
        assert rows.count() > 0, "les Skills installes doivent apparaitre"
        # Les sections sont nommées en français et l'origine est lisible.
        expect(page.locator("#skills-manager .skill-section-title").first).to_be_visible()
        expect(page.locator("#skills-manager .skill-origin").first).to_have_text(
            "fourni", timeout=15000
        )
        expect(page.locator("#skills-manager .skill-toggle").first).to_be_checked()
        hint = page.locator("#skills-hint").inner_text()
        assert "Skills" in hint and "désactivé" in hint, hint
    finally:
        context.close()


def test_unchecking_a_skill_survives_a_reload(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context = browser.new_context()
    page = context.new_page()
    page.goto(tools_panel(server), wait_until="domcontentloaded")
    try:
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        row = page.locator("#skills-manager .skill-row").first
        expect(row).to_be_visible(timeout=20000)
        name = row.get_attribute("data-skill")
        box = row.locator("input.skill-toggle")
        expect(box).to_be_checked()

        box.uncheck()
        expect(box).not_to_be_checked(timeout=15000)
        expect(page.locator("#skills-hint")).to_contain_text("désactivé", timeout=15000)

        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#input")).to_be_visible(timeout=30000)
        again = page.locator(f"#skills-manager .skill-row[data-skill='{name}'] input.skill-toggle")
        expect(again).not_to_be_checked(timeout=20000), "le choix doit survivre au rafraichissement"

        # Remise en etat : le test ne laisse pas l'installation dans un etat modifie.
        again.check()
        expect(again).to_be_checked(timeout=15000)
    finally:
        context.close()


def test_disabling_everything_then_reenabling(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context = browser.new_context()
    page = context.new_page()
    page.goto(tools_panel(server), wait_until="domcontentloaded")
    try:
        expect(page.locator("#skills-manager .skill-row").first).to_be_visible(timeout=20000)
        page.click("#skills-disable-all")
        expect(page.locator("#skills-manager .skill-toggle").first).not_to_be_checked(timeout=20000)
        expect(page.locator("#skills-manager .skill-row.off").first).to_be_visible(timeout=15000)

        page.click("#skills-enable-all")
        # La ligne d'état n'affiche le total qu'une fois TOUS les PATCH retombés : attendre
        # le DOM seul laissait la course ouverte sur un runner chargé (mesuré en CI
        # Windows, où la moitié des lignes restait « éteinte »).
        expect(page.locator("#skills-hint")).to_contain_text("activé", timeout=30000)
        expect(page.locator("#skills-manager .skill-toggle").first).to_be_checked(timeout=20000)
        expect(page.locator("#skills-manager .skill-row.off")).to_have_count(0, timeout=15000)
    finally:
        context.close()
