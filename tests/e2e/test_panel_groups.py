"""Les cinq groupes du panneau de droite, prouvés à l'écran (P3-16b).

Les tests précédents lisaient le source : ils ont dit « onglets regroupés » alors que
la colonne empilait encore douze boutons et collait « ConnexionsNouveau ». Ici on lit
l'état rendu — quels boutons existent, ce qui est visible après un clic, combien de
lignes occupe la barre.

    pytest -o addopts="" -m e2e tests/e2e/test_panel_groups.py -q
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402


def open_panel(page) -> None:
    """Ouvre la colonne de configuration (elle est repliée sous 1100 px)."""
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.wait_for_timeout(200)


def visible_subtabs(page) -> list[str]:
    """Le libellé de la section, sans la pastille (« Nouveau ») qui le suit."""
    return page.eval_on_selector_all(
        "#panel-subtabs .panel-subtab",
        """els => els.filter(el => getComputedStyle(el).display !== 'none').map(el => {
            const copy = el.cloneNode(true);
            copy.querySelectorAll('.tab-badge').forEach(badge => badge.remove());
            return copy.textContent.trim();
        })""",
    )


def shown_views(page) -> list[str]:
    return page.eval_on_selector_all(
        ".panel-view",
        "els => els.filter(el => !el.hidden).map(el => el.id)",
    )


def test_the_strip_has_five_groups_and_nothing_else(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_panel(page)
        labels = page.eval_on_selector_all(
            "#panel-groups .panel-tab", "els => els.map(el => el.textContent.trim())"
        )
        assert labels == [
            "Projets",
            "Décision",
            "Outils & Connexions",
            "Cerveau & Mémoire",
            "Modèles & Fournisseurs",
        ], f"cinq groupes, pas douze onglets : {labels}"
        # Un seul groupe est actif à l'ouverture.
        active = page.eval_on_selector_all(
            "#panel-groups .panel-tab.active", "els => els.map(el => el.id)"
        )
        assert len(active) == 1, f"un seul groupe actif : {active}"
    finally:
        context.close()


def test_a_group_opens_its_sections(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_panel(page)
        page.click("#group-tools")
        assert visible_subtabs(page) == ["Outils", "MCP", "UHB", "Infra", "Tâches", "Extensions"]
        page.click("#tab-conn")
        assert shown_views(page) == ["conn-view"], f"une section à la fois : {shown_views(page)}"
        page.click("#tab-uhb")
        assert shown_views(page) == ["uhb-view"]
        page.click("#group-brain")
        assert visible_subtabs(page) == ["Mémoire", "Télémétrie", "Données"]
        page.click("#tab-telemetry")
        assert shown_views(page) == ["telemetry-view"]
    finally:
        context.close()


def test_the_projects_group_opens_git_and_files(browser, server) -> None:
    """Projets n'a plus une seule section : Git & runs et Fichiers vivent dans le même
    groupe, et la barre des sections doit les montrer tous les deux."""
    context, page = open_app(browser, server)
    try:
        open_panel(page)
        page.click("#group-projects")
        assert page.eval_on_selector("#panel-subtabs", "el => el.hidden") is False
        assert visible_subtabs(page) == ["Git & runs", "Fichiers"]
        page.click("#tab-files")
        assert shown_views(page) == ["files-view"]
        page.click("#tab-projects")
        assert shown_views(page) == ["projects-view"]
    finally:
        context.close()


def test_the_memory_graph_is_in_the_brain_not_in_uhb(browser, server) -> None:
    """« Second brain » et « Memory graph » étaient deux sujets dans un onglet."""
    context, page = open_app(browser, server)
    try:
        open_panel(page)
        page.click("#group-brain")
        page.click("#tab-memory")
        assert (
            page.eval_on_selector("#memory-graph", "el => el.closest('.panel-view').id")
            == "memory-view"
        )
        page.click("#group-tools")
        page.click("#tab-uhb")
        assert page.eval_on_selector_all("#uhb-view #memory-graph", "els => els.length") == 0
    finally:
        context.close()


def test_the_badge_stays_on_one_line(browser, server) -> None:
    """La barre des sections : deux rangées compactes au plus, jamais de débordement.

    Six sections (Outils & Connexions) ne tiennent pas sur une ligne dans 319 px avec les
    polices de Linux : deux rangées sont admises à condition qu'elles restent compactes.
    Trois rangées mesureraient ~78 px, d'où le plafond de 60 px - il refuse la troisième
    rangée, il n'autorise pas une barre qui pousse le contenu hors du panneau.
    """
    context, page = open_app(browser, server)
    try:
        open_panel(page)
        page.click("#group-tools")
        bar = page.eval_on_selector(
            "#panel-subtabs",
            """el => ({ height: el.getBoundingClientRect().height,
                        scrollWidth: el.scrollWidth, clientWidth: el.clientWidth })""",
        )
        assert bar["height"] <= 60, f"deux rangées compactes au plus (trois ≈ 78 px) : {bar}"
        # La puce MCP elle-même reste sur UNE ligne malgré son badge (pastille de 6 px,
        # mesurée à 20 px de haut) : si elle se repliait, cette hauteur doublerait.
        conn = page.eval_on_selector("#tab-conn", "el => el.getBoundingClientRect().height")
        assert conn <= 40, f"le bouton reste sur une ligne malgré son badge : {conn}"
        assert bar["scrollWidth"] <= bar["clientWidth"] + 8, "pas de débordement horizontal"
    finally:
        context.close()


def test_the_group_remembers_its_last_section(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        open_panel(page)
        page.click("#group-models")
        page.click("#tab-local")
        page.click("#group-tools")
        page.click("#group-models")
        assert shown_views(page) == ["local-view"], "le groupe rouvre sa dernière section"
    finally:
        context.close()
