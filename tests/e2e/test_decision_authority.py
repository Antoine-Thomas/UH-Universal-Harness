"""P2-10 à l'écran : la vue Décision dit qui décide du routage.

UH n'a pas de second avis à donner : la politique appartient à UHB. Ce que la vue
doit rendre visible, c'est **qui** a décidé — et, quand UHB n'est pas là, le fait
qu'aucune politique n'a été consultée (le tour est décrit, pas expliqué).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

from .conftest import open_app  # noqa: E402

DECISION = {
    "session_id": "s1",
    "available": True,
    "prompt": {
        "complexity": None,
        "mode": None,
        "reason": None,
        "note": "UHB n'a pas été consulté pour ce tour : aucun appel uhb_route",
    },
    "model": {"model": "qwen2.5:7b", "provider": "ollama", "chosen": "qwen2.5:7b"},
    "chain": [],
    "context": {"sent": 1200, "budget": 24000},
    "usage": {"tokens_in": 1200, "tokens_out": 90},
}


def mock_decision(page, payload=None) -> None:
    page.route(
        "**/api/sessions/*/decision",
        lambda route: route.fulfill(status=200, json=payload or DECISION),
    )


def mock_status(page, state: str, mode: str = "chat") -> None:
    body = {
        "connected": state == "ok",
        "state": state,
        "mode": mode,
        "sentence": "test",
    }
    page.route("**/api/uhb/status", lambda route: route.fulfill(status=200, json=body))


def open_decision(page) -> None:
    if page.eval_on_selector("#providers-panel", "el => el.classList.contains('collapsed')"):
        page.click("#providers-open")
        page.wait_for_timeout(200)
    page.click("#group-decision")
    page.wait_for_timeout(150)
    page.click("#tab-decision")


def seed_session(page, server) -> None:
    """Une session réelle, sinon la vue refuse de se remplir."""
    page.fill("#input", "bonjour")
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)


def test_a_connected_uhb_owns_the_policy(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        seed_session(page, server)
        mock_status(page, "ok", mode="dev")
        mock_decision(page)
        open_decision(page)
        page.wait_for_selector("#decision-panel .decision-authority", timeout=20000)

        line = page.locator("#decision-panel .decision-authority").first
        assert "appartient à UHB" in line.inner_text()
        assert "mode dev" in line.inner_text()
        assert line.get_attribute("data-uhb-state") == "ok"
    finally:
        context.close()


def test_an_absent_uhb_means_no_policy_at_all(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        seed_session(page, server)
        mock_status(page, "absent")
        mock_decision(page)
        open_decision(page)
        page.wait_for_selector("#decision-panel .decision-authority", timeout=20000)

        line = page.locator("#decision-panel .decision-authority").first
        assert "UHB n'est pas consulté" in line.inner_text()
        assert "pas de politique de routage" in line.inner_text()
        assert line.get_attribute("data-uhb-state") == "absent"
        assert "warn" in (line.get_attribute("class") or "")
    finally:
        context.close()


def test_a_broken_uhb_is_not_presented_as_a_policy(browser, server) -> None:
    context, page = open_app(browser, server)
    try:
        seed_session(page, server)
        mock_status(page, "error")
        mock_decision(page)
        open_decision(page)
        page.wait_for_selector("#decision-panel .decision-authority", timeout=20000)

        line = page.locator("#decision-panel .decision-authority").first
        assert "en erreur" in line.inner_text()
        assert "décrit le tour" in line.inner_text()
        assert line.get_attribute("data-uhb-state") == "error"
    finally:
        context.close()
