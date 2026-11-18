"""Lot 3 — le cycle de vie d'un Skill dans le vrai navigateur.

Creer, modifier, dupliquer, supprimer (avec annulation), importer un .md, et refuser
un fichier non conforme sans abimer la liste. Aucun de ces tests ne remplace un
parcours : ils cliquent, ils relisent le contenu sauvegarde, ils rechargent.

`uh web` sert `script-src 'self'` : attentes par `expect(locator)`, jamais
`page.wait_for_function`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


def panel(server) -> str:
    return f"{server.url}{'&' if '?' in server.url else '?'}panel=tools"


def row(page, name: str):
    return page.locator(f"#skills-manager .skill-row[data-skill='{name}']")


def open_panel(browser, server):
    context = browser.new_context()
    page = context.new_page()
    page.goto(panel(server), wait_until="domcontentloaded")
    expect(page.locator("#skills-manager .skill-row").first).to_be_visible(timeout=25000)
    return context, page


def create_skill(page, name: str, title: str, instructions: str) -> None:
    page.click("#skill-new")
    expect(page.locator("#skill-editor")).to_be_visible(timeout=10000)
    page.fill("#skill-name", name)
    page.fill("#skill-title", title)
    page.fill("#skill-description", "cree depuis l'interface")
    page.fill("#skill-instructions", instructions)
    page.click("#skill-save")
    expect(row(page, name)).to_be_visible(timeout=20000)


def test_creating_a_skill_then_checking_it(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context, page = open_panel(browser, server)
    try:
        create_skill(page, "savoir-test", "Savoir de test", "Verifie toujours par le calcul.")
        expect(row(page, "savoir-test").locator(".skill-name")).to_have_text("Savoir de test", timeout=10000)
        box = row(page, "savoir-test").locator("input.skill-toggle")
        expect(box).to_be_checked()
        box.uncheck()
        expect(box).not_to_be_checked(timeout=15000)
        box.check()
        expect(box).to_be_checked(timeout=15000)
    finally:
        context.close()


def test_editing_a_skill_and_reading_it_back(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context, page = open_panel(browser, server)
    try:
        create_skill(page, "savoir-edite", "Titre initial", "Instructions initiales.")
        row(page, "savoir-edite").locator("button.skill-action", has_text="Modifier").click()
        expect(page.locator("#skill-editor")).to_be_visible(timeout=10000)
        expect(page.locator("#skill-instructions")).to_have_value("Instructions initiales.", timeout=10000)
        page.fill("#skill-title", "Titre modifie")
        page.fill("#skill-instructions", "Instructions modifiees par le test.")
        page.click("#skill-save")
        expect(row(page, "savoir-edite").locator(".skill-name")).to_have_text("Titre modifie", timeout=20000)

        # Relecture apres rechargement : le contenu sauvegarde est bien celui-la.
        page.reload(wait_until="domcontentloaded")
        expect(page.locator("#skills-manager .skill-row").first).to_be_visible(timeout=25000)
        row(page, "savoir-edite").locator("button.skill-action", has_text="Modifier").click()
        expect(page.locator("#skill-instructions")).to_have_value(
            "Instructions modifiees par le test.", timeout=15000
        )
    finally:
        context.close()


def test_duplicating_a_skill_leaves_the_original_untouched(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context, page = open_panel(browser, server)
    try:
        create_skill(page, "savoir-copie", "Original", "Contenu original.")
        row(page, "savoir-copie").locator("button.skill-action", has_text="Dupliquer").click()
        copy = row(page, "savoir-copie-copie")
        expect(copy).to_be_visible(timeout=20000)
        expect(row(page, "savoir-copie").locator(".skill-name")).to_have_text("Original")
        copy.locator("button.skill-action", has_text="Modifier").click()
        expect(page.locator("#skill-instructions")).to_have_value("Contenu original.", timeout=10000)
    finally:
        context.close()


def test_deleting_asks_for_confirmation(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context, page = open_panel(browser, server)
    try:
        create_skill(page, "savoir-jete", "A jeter", "Temporaire.")
        # Annulation : rien ne disparait.
        page.once("dialog", lambda dialog: dialog.dismiss())
        row(page, "savoir-jete").locator("button.skill-action", has_text="Supprimer").click()
        expect(row(page, "savoir-jete")).to_be_visible(timeout=10000)

        # Confirmation : la ligne disparait.
        page.once("dialog", lambda dialog: dialog.accept())
        row(page, "savoir-jete").locator("button.skill-action", has_text="Supprimer").click()
        expect(row(page, "savoir-jete")).to_have_count(0, timeout=20000)
    finally:
        context.close()


def test_importing_a_markdown_skill_and_refusing_a_bad_file(
    make_server, browser, tmp_path: Path
) -> None:
    server = make_server("home")
    context, page = open_panel(browser, server)
    good = tmp_path / "savoir-importe.md"
    good.write_text(
        "# Savoir importe\n\n> Importe par le test.\n\n## Instructions\n\nFais ceci.\n",
        encoding="utf-8",
    )
    bad = tmp_path / "sans-titre.md"
    bad.write_text("pas un skill\n", encoding="utf-8")
    try:
        page.set_input_files("#skill-file", str(good))
        expect(row(page, "savoir-importe")).to_be_visible(timeout=20000)
        expect(row(page, "savoir-importe").locator(".skill-origin")).to_have_text("perso")

        before = page.locator("#skills-manager .skill-row").count()
        # On attend la REPONSE du serveur, pas l'apparition d'un element : sans cela
        # l'assertion peut courir avant que le 400 ne revienne, et elle mesure une
        # course, pas un comportement.
        with page.expect_response(
            lambda r: "/api/skills/import" in r.url and r.status == 400, timeout=20000
        ) as refused:
            page.set_input_files("#skill-file", str(bad))
        assert "file name" in refused.value.text(), refused.value.text()
        expect(page.locator("#skill-error")).to_be_visible(timeout=15000)
        assert page.locator("#skills-manager .skill-row").count() == before, (
            "un fichier refuse ne doit pas alterer la liste"
        )
    finally:
        context.close()


def test_a_native_skill_offers_no_edit_or_delete(make_server, browser, tmp_path: Path) -> None:
    server = make_server("home")
    context, page = open_panel(browser, server)
    try:
        native = page.locator("#skills-manager .skill-row[data-origin='native']").first
        expect(native).to_be_visible(timeout=20000)
        assert native.locator("button.skill-action", has_text="Modifier").count() == 0
        assert native.locator("button.skill-action", has_text="Supprimer").count() == 0
        # Dupliquer reste possible : c'est le contrat du serveur.
        expect(native.locator("button.skill-action", has_text="Dupliquer")).to_be_visible()
    finally:
        context.close()
