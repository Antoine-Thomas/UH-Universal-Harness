"""Le panneau Machine, prouvé à l'écran (G19, étape 5).

Ce que ces tests mesurent, et rien d'autre :

* le profil affiché vient du scan (UHB), et les champs non mesurés sont dits
  « non mesuré » — jamais comblés par une valeur plausible ;
* l'alternative qui ne tient pas en VRAM porte son avertissement, son badge
  « à vérifier », une case **décochée** et un bouton désactivé ;
* **aucune** requête `/api/models/pull` ne part avant un clic explicite, et le
  clic en envoie exactement une, avec `accept: true`.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402

SCAN = {
    "profile": {
        "os": "Windows",
        "arch": "AMD64",
        "python": "3.10.0",
        "cpu": {"model": "test", "cores": 16},
        "ram": {"total_gb": 63.9, "free_gb": 38.4, "source": "test"},
        "disk": {"home_free_gb": None},
        "gpu": {
            "vendor": "nvidia",
            "name": "NVIDIA GeForce RTX 3070 Ti",
            "vram_total_gb": 8.0,
            "vram_free_gb": 6.05,
            "source": "nvidia-smi",
        },
        "unknown": ["disk_home_free_gb"],
    },
    "catalog": {"version": "1", "updated": "2026-11-27", "count": 5},
    "entry": {
        "id": "qwen2.5:7b",
        "mode": "vram",
        "warning": None,
        "default_checked": True,
        "verified": True,
        "license": "Apache-2.0",
        "file_gb": 4.7,
        "needs": {"vram_gb": 5.7, "ram_gb": 0.0, "disk_gb": 4.7},
    },
    "alternatives": [
        {
            "id": "inkling-2-mini:26b-a5b-iq4_NL",
            "mode": "offload",
            "warning": "ne tient pas en 8 Go de VRAM : experts en RAM, lenteur non mesurée",
            "default_checked": False,
            "verified": False,
            "license": "Apache-2.0",
            "file_gb": 18.0,
            "needs": {"vram_gb": 19.3, "ram_gb": 62.0, "disk_gb": 18.0},
        }
    ],
    "reasons": ["6.05 Go de VRAM libres : qwen2.5:7b tient avec la marge de 0.8 Go"],
}


def open_panel(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
    page.wait_for_timeout(200)
    page.click("#group-models")
    page.wait_for_timeout(150)
    page.click("#tab-machine")


def mock_scan(page, payload=None):
    page.route(
        "**/api/machine/scan",
        lambda route: route.fulfill(status=200, json=payload or SCAN),
    )


def test_the_panel_shows_what_was_measured_and_names_the_blanks(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_scan(page)
        open_panel(page)
        page.wait_for_selector("#machine-panel .machine-entry", timeout=20000)

        panel = page.locator("#machine-panel")
        assert "NVIDIA GeForce RTX 3070 Ti" in panel.inner_text()
        assert "6.05 Go libres sur 8 Go" in panel.inner_text()
        assert "Non mesuré, donc jamais supposé : disk_home_free_gb" in panel.inner_text()
        assert "Non mesuré" in panel.inner_text() or "non mesuré" in panel.inner_text()

        recommended = page.locator('.machine-entry[data-entry-id="qwen2.5:7b"]')
        assert recommended.inner_text().count("qwen2.5:7b") >= 1
        button = recommended.locator("button[data-machine-download]")
        assert button.inner_text() == "Télécharger (4.7 Go)"
        assert button.is_enabled()
    finally:
        context.close()


def test_the_alternative_is_warned_badged_unchecked_and_locked(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        mock_scan(page)
        open_panel(page)
        page.wait_for_selector("#machine-panel .machine-entry-alt", timeout=20000)

        alt = page.locator('.machine-entry-alt[data-entry-id="inkling-2-mini:26b-a5b-iq4_NL"]')
        text = alt.inner_text()
        assert "ne tient pas en 8 Go de VRAM" in text
        assert "lenteur non mesurée" in text
        assert "à vérifier" in text
        assert "18 Go sur disque" in text

        box = alt.locator("input[type=checkbox]")
        assert box.is_checked() is False, "une case jamais cochée d'avance"
        button = alt.locator("button[data-machine-download]")
        assert button.is_disabled(), "le bouton attend la case, pas un défaut"

        box.check()
        assert button.is_enabled()
    finally:
        context.close()


def test_nothing_downloads_without_a_click_then_exactly_one_does(browser, server) -> None:
    context, page = open_app(browser, server)
    calls: list[dict] = []
    try:
        mock_scan(page)

        def pull(route):
            calls.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(
                status=202,
                json={
                    "started": True,
                    "id": "run-1",
                    "mode": "vram",
                    "status_url": "/models/pull/run-1",
                },
            )

        page.route("**/api/machine/pull", pull)
        open_panel(page)
        page.wait_for_selector("#machine-panel .machine-entry", timeout=20000)
        page.wait_for_timeout(800)

        assert calls == [], "un scan ne télécharge rien : aucune requête avant le clic"

        page.locator(
            '.machine-entry[data-entry-id="qwen2.5:7b"] button[data-machine-download]'
        ).click()
        page.wait_for_selector("#machine-panel .machine-progress", timeout=20000)

        assert calls == [{"entry_id": "qwen2.5:7b", "accept": True}]
        assert "Téléchargement lancé (4.7 Go)" in page.locator("#machine-panel").inner_text()
    finally:
        context.close()


def test_an_absent_uhb_is_said_in_french(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        page.route(
            "**/api/machine/scan",
            lambda route: route.fulfill(
                status=503,
                json={
                    "detail": "UHB n'est pas configuré : le panneau Machine lit le "
                    "profil et le catalogue par son API"
                },
            ),
        )
        open_panel(page)
        page.wait_for_selector("#machine-panel .machine-warning", timeout=20000)

        assert "UHB n'est pas configuré" in page.locator("#machine-panel").inner_text()
        assert page.locator("button[data-machine-download]").count() == 0
    finally:
        context.close()
