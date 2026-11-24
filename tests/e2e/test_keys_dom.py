"""A stored key never appears in the browser, and it can be deleted from the panel.

Two things this pins, both of which were broken:
- a revoked key stayed on screen for ever, because the route existed (DELETE by
  index) and the interface had no button;
- and the value itself must never reach the DOM, a response, storage or the journal -
  a masked hint is all the interface is allowed to know.

The delete flow needs a real browser (confirm dialog, re-render after the call), so
this is an e2e test even though the leak half could be checked without one.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e

#: Recognisable, and shaped like a real DeepSeek key: if it leaks, this exact string
#: shows up somewhere and grep finds it.
CANARY = "sk-uhe2e-CANARY-7f31d0"
PROVIDER = "deepseek"


def test_a_key_never_reaches_the_dom_and_can_be_deleted(
    tmp_path: Path, server: Server, browser
) -> None:
    auth = {"Authorization": f"Bearer {server.token()}"}
    added = httpx.post(
        f"{server.base}/api/providers/{PROVIDER}/keys",
        json={"key": CANARY},
        headers=auth,
        timeout=10,
    )
    assert added.status_code == 200, added.text
    assert CANARY not in added.text, "the add route already must not echo the key"

    seen: list[str] = []
    context, page = open_app(browser, server)
    page.on(
        "response",
        lambda response: seen.append(f"{response.url} :: {_body(response)}"),
    )

    # Every panel: a hint could leak in the one nobody opens.
    for tab in (
        "keys",
        "local",
        "models",
        "uhb",
        "memory",
        "decision",
        "telemetry",
        "conn",
        "data",
    ):
        page.evaluate(f"document.getElementById('tab-{tab}').click()")
        page.wait_for_timeout(400)
    page.evaluate("document.getElementById('tab-keys').click()")
    page.wait_for_timeout(1200)

    page_text = page.inner_text("body")
    storage = page.evaluate(
        "JSON.stringify({local: Object.assign({}, localStorage), session: Object.assign({}, sessionStorage)})"
    )

    assert CANARY not in page_text, "the key is in the page text"
    assert CANARY not in storage, "the key is in browser storage"
    assert CANARY not in "".join(seen), "the key came back in a response body"

    journal_file = server.home / "uh-web.log"
    if journal_file.is_file():
        assert CANARY not in journal_file.read_text(encoding="utf-8", errors="replace")

    # The masked hint is shown, with the delete button that was missing.
    # Le panneau rend ses lignes après son GET /api/providers : compter la liste une
    # seule fois, à date fixe, lisait l'état d'avant le rendu — c'est l'échec de
    # `e2e / windows-latest` du 24/11 (« no row for the stored key »), reproduit de
    # façon déterministe en retardant cette réponse de 3 s. On attend la ligne.
    rows = page.locator(".key-row")
    expect(rows).to_have_count(1, timeout=15000)
    remove = page.get_by_role("button", name="Supprimer la clé 1 de deepseek")
    expect(remove).to_be_visible(timeout=15000)

    page.on("dialog", lambda dialog: dialog.accept())
    # La suppression est asynchrone : la réponse du DELETE est le seul repère sûr
    # avant d'interroger le serveur, pas un délai de deux secondes.
    with page.expect_response(
        lambda response: (
            response.request.method == "DELETE"
            and f"/api/providers/{PROVIDER}/keys" in response.url
        )
    ) as deleted:
        remove.click()
    assert deleted.value.ok, deleted.value.status

    remaining = httpx.get(f"{server.base}/api/providers", headers=auth, timeout=10).json()
    keys = (remaining.get("keys") or {}).get(PROVIDER) or {}
    assert not keys.get("key_hints"), f"the key is still stored: {keys}"
    expect(rows).to_have_count(0, timeout=15000)
    context.close()


def _body(response) -> str:
    """The body of a JSON/text response, or "" - never fails a test on a binary one."""
    try:
        kind = (response.headers.get("content-type") or "").lower()
        if "json" in kind or "text" in kind or "html" in kind:
            return response.text()
    except Exception:  # noqa: BLE001 - a body we cannot read is not a body we assert on
        return ""
    return ""
