"""Le gestionnaire d'extensions à l'écran (P3-16d / D-8).

Trois familles visibles, une bascule qui prend effet sans redémarrer `uh web`, une épreuve
qui réussit sur un skill valide et qui **explique** son refus sur une extension cassée
(binaire absent, skill sans instructions).

    pytest -o addopts="" -m e2e tests/e2e/test_extensions_panel.py -q
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


@pytest.fixture()
def seeded(make_server, tmp_path: Path):
    """Un skill valide, un skill vide (refus attendu) et un serveur MCP au binaire absent."""
    server = make_server()
    skills = server.home / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "veille.md").write_text(
        "# Veille\n\nRésume les flux RSS du matin en trois lignes.\n", encoding="utf-8"
    )
    (skills / "vide.md").write_text("# Vide\n", encoding="utf-8")
    (server.home / "config.toml").write_text(
        '[[mcp.servers]]\nname = "casse"\ncommand = "uh-binaire-qui-nexiste-pas-42"\nargs = []\n',
        encoding="utf-8",
    )
    return server


def open_extensions(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.click("#group-tools")
    page.click("#tab-extensions")
    page.wait_for_selector("#extensions-groups .extension-group", timeout=25000)


def row_selector(kind: str, name: str) -> str:
    return f"#extensions-groups .extension-row[data-kind='{kind}'][data-name='{name}']"


def test_the_three_families_are_on_one_screen(browser, seeded) -> None:
    context, page = open_app(browser, seeded)
    try:
        open_extensions(page)
        kinds = page.eval_on_selector_all(
            "#extensions-groups .extension-group", "els => els.map(e => e.dataset.kind)"
        )
        assert kinds == ["mcp", "skills", "uhb"], kinds
        names = page.eval_on_selector_all(
            "#extensions-groups .extension-row",
            "els => els.map(e => [e.dataset.kind, e.dataset.name])",
        )
        assert ["skills", "veille"] in names
        assert ["skills", "vide"] in names
        assert ["mcp", "casse"] in names
        assert any(kind == "uhb" for kind, _ in names)
        assert page.eval_on_selector("#extensions-mode", "el => el.textContent").count("/") == 1
    finally:
        context.close()


def test_turning_a_skill_off_is_effective_without_a_restart(browser, seeded) -> None:
    context, page = open_app(browser, seeded)
    try:
        open_extensions(page)
        row = row_selector("skills", "veille")
        assert page.eval_on_selector(f"{row} .task-flag", "el => el.textContent") == "activée"

        page.click(f"{row} button[data-action='toggle']")
        page.wait_for_function(
            f"() => document.querySelector(\"{row}\")?.dataset.enabled === '0'", timeout=20000
        )
        assert page.eval_on_selector(f"{row} .task-flag", "el => el.textContent") == "éteinte"
        hint = page.eval_on_selector("#extensions-hint", "el => el.textContent")
        assert "sans redémarrage" in hint, hint

        # Le serveur n'a pas bougé : la même instance rend maintenant le skill éteint.
        api = httpx.get(
            seeded.base + "/api/extensions",
            headers={"Authorization": f"Bearer {seeded.token()}"},
            timeout=20,
        ).json()
        entries = {entry["name"]: entry for entry in api["groups"]["skills"]}
        assert entries["veille"]["enabled"] is False
    finally:
        context.close()


def test_a_working_extension_passes_its_test(browser, seeded) -> None:
    context, page = open_app(browser, seeded)
    try:
        open_extensions(page)
        page.click(f"{row_selector('skills', 'veille')} button[data-action='test']")
        page.wait_for_function(
            "() => (document.querySelector('#extensions-hint')?.textContent || '').includes('ok')",
            timeout=25000,
        )
        hint = page.eval_on_selector("#extensions-hint", "el => el.textContent")
        assert "veille" in hint and "caractères" in hint, hint
    finally:
        context.close()


def test_a_broken_extension_explains_its_refusal(browser, seeded) -> None:
    """Deux refus différents, chacun nommé : skill sans instructions, binaire absent."""
    context, page = open_app(browser, seeded)
    try:
        open_extensions(page)

        page.click(f"{row_selector('skills', 'vide')} button[data-action='test']")
        page.wait_for_function(
            "() => (document.querySelector('#extensions-hint')?.textContent || '').includes('refusé')",
            timeout=25000,
        )
        hint = page.eval_on_selector("#extensions-hint", "el => el.textContent")
        assert "instruction" in hint, hint

        page.click(f"{row_selector('mcp', 'casse')} button[data-action='test']")
        page.wait_for_function(
            "() => (document.querySelector('#extensions-hint')?.textContent || '')"
            ".includes('binaire introuvable')",
            timeout=40000,
        )
        hint = page.eval_on_selector("#extensions-hint", "el => el.textContent")
        assert "refusé" in hint and "binaire introuvable" in hint, hint
    finally:
        context.close()
