"""P1-13 in a real browser: the chart lives in the bubble, and survives a reload.

Two sides of the same contract:

* a **stored** message carrying an ``artifacts`` payload is drawn when the session
  is opened — and drawn again after a reload, which is what "persisted" means;
* the live ``artifacts`` event a turn emits (what ``execute_code`` produces once the
  snippet wrote a PNG) draws into the assistant bubble it belongs to.

The turn is routed at the network layer (canned SSE), so the browser exercises its
real code path without a model, a key or a GPU — same technique as the P1-12 test.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from uh.store import Store

from .conftest import open_app

pytestmark = pytest.mark.e2e

#: A 1x1 PNG: the smallest image the browser actually decodes.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
ARTIFACT = {
    "mime": "image/png",
    "bytes": len(PNG),
    "data_url": "data:image/png;base64," + base64.b64encode(PNG).decode(),
}
SSE = (
    "event: open\ndata: {}\n\n"
    'event: token\ndata: {"text": "Voici la courbe."}\n\n'
    "event: artifacts\ndata: " + json.dumps({"images": [ARTIFACT]}) + "\n\n"
    "event: done\ndata: {}\n\n"
)
CHART = "img.tool-artifact-img"


def seed(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True)
    with Store(home / "uh.db") as store:
        session_id = store.create_session(title="graphe", model="stub:1b", provider="ollama")
        store.append_message(session_id, {"role": "user", "content": "trace la courbe de y = x^2"})
        store.append_message(
            session_id,
            {"role": "assistant", "content": "Voici la courbe.", "artifacts": [ARTIFACT]},
        )
    return session_id


def test_a_stored_chart_is_drawn_and_survives_a_reload(
    make_server, browser, tmp_path: Path
) -> None:
    seed(tmp_path / "home")
    server = make_server("home")
    context, page = open_app(browser, server)
    try:
        first = page.locator(f"#messages {CHART}").first
        first.wait_for(state="visible", timeout=15000)
        assert (first.get_attribute("src") or "").startswith("data:image/png;base64,")

        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#input", timeout=30000)
        again = page.locator(f"#messages {CHART}").first
        again.wait_for(state="visible", timeout=20000)
        assert (again.get_attribute("src") or "").startswith("data:image/png;base64,"), (
            "l'image doit etre redessinee depuis SQLite apres rechargement"
        )
    finally:
        context.close()


def test_the_live_artifacts_event_draws_the_chart_in_the_bubble(
    make_server, browser, tmp_path: Path
) -> None:
    server = make_server("home")
    context, page = open_app(browser, server)
    page.route(
        "**/api/sessions/*/chat",
        lambda route: route.fulfill(
            status=200, headers={"Content-Type": "text/event-stream"}, body=SSE
        ),
    )
    try:
        page.fill("#input", "trace la courbe de y = x^2")
        page.press("#input", "Enter")
        image = page.locator(f"#messages {CHART}").first
        image.wait_for(state="visible", timeout=20000)
        assert (image.get_attribute("src") or "").startswith("data:image/png;base64,")
        assert "Voici la courbe." in page.inner_text("#messages")
        assert page.locator("#messages .msg.failed").count() == 0
    finally:
        context.close()
