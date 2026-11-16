"""The Fournisseurs panel: it loads on the first click, once, and alone.

Two defects this pins, both measurable in a real browser:

- **three entry points, three renders.** Opening the page with ``?panel=keys``
  calls ``openPanelFromUrl()`` -> ``openProviders()`` and then
  ``showPanelTab("keys")`` -> ``openProviders()`` again. Both fired without
  knowing a load was in flight, so ``/api/providers`` was asked twice and the
  slower answer won the last render.
- **a neighbour's failure emptied the panel.** ``renderRouterSettings(await
  api("/api/router/settings"))`` sat inside the same chain as the provider
  render, so a 500 on the router settings wiped a list that had already loaded
  and displayed "error" over an empty box - which reads as "you have no
  providers".

Neither needs a model, a key or the network: the e2e harness serves a real
``uh web`` on a free port with its own ``UH_HOME``.
"""

from __future__ import annotations

import pytest

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e


def _panel_url(server: Server) -> str:
    # The server prints `/?token=…`: the panel parameter rides with it.
    return f"{server.url}&panel=keys"


def _wait_for_cards(page) -> None:
    """Wait for a rendered provider, and say what the panel shows when it does not.

    A bare `wait_for_selector` timeout says "nothing appeared" and hides the one
    fact that identifies the cause: whether the panel is empty, or holding an
    error, or never opened.
    """
    try:
        page.wait_for_selector("#providers-list .provider-card", timeout=15000)
    except Exception as exc:  # noqa: BLE001 - re-raised with the panel's own text
        try:
            shown = page.inner_text("#providers-panel")[:800]
        except Exception:  # noqa: BLE001
            shown = "(panel absent)"
        raise AssertionError(f"no provider card rendered; the panel says: {shown!r}") from exc


def test_the_panel_loads_once_and_renders_on_the_first_click(
    tmp_path, server: Server, browser
) -> None:
    context = browser.new_context()
    page = context.new_page()
    asked: list[str] = []
    page.on(
        "request",
        lambda request: asked.append(request.url) if "/api/providers" in request.url else None,
    )
    try:
        page.goto(_panel_url(server), wait_until="domcontentloaded")
        _wait_for_cards(page)

        provider_calls = [url for url in asked if url.split("?")[0].endswith("/api/providers")]
        assert len(provider_calls) == 1, (
            "the two entry points must share one request, got: " + repr(provider_calls)
        )
        # The loading state is observable, and it is over.
        assert page.get_attribute("#providers-panel", "aria-busy") == "false"
        # Something was really rendered, not just "no error".
        assert page.locator("#providers-list .provider-card").count() >= 1
    finally:
        context.close()


def test_a_failing_router_settings_call_leaves_the_provider_list_intact(
    tmp_path, server: Server, browser
) -> None:
    context = browser.new_context()
    page = context.new_page()
    page.route(
        "**/api/router/settings",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"detail": "router settings are broken on purpose"}',
        ),
    )
    try:
        page.goto(_panel_url(server), wait_until="domcontentloaded")
        _wait_for_cards(page)

        assert page.locator("#providers-list .provider-card").count() >= 1, (
            "a neighbour's 500 must not empty the provider list"
        )
        assert "indisponibles" in page.inner_text("#router-settings"), (
            "the failure is reported in its own box"
        )
        hint = page.inner_text("#providers-hint")
        assert "erreur de chargement" not in hint, (
            f"the providers loaded, so the hint must not claim otherwise: {hint!r}"
        )
    finally:
        context.close()


def test_reopening_the_panel_after_a_settled_load_refetches(
    tmp_path, server: Server, browser
) -> None:
    """Coalescing is about *concurrent* calls: a later click must see fresh state.

    Every mutation (add a key, delete a key, change a provider) awaits the same
    loader; caching a successful answer would show a stale list after a write.
    """
    context, page = open_app(browser, server)
    asked: list[str] = []
    page.on(
        "request",
        lambda request: asked.append(request.url) if "/api/providers" in request.url else None,
    )
    try:
        page.evaluate("document.getElementById('tab-keys').click()")
        page.wait_for_timeout(1200)
        assert asked, "a click after the first load must ask again"
    finally:
        context.close()
