"""E1-E3: the three defects the reviewer reproduced, driven in a real Chromium.

Each test states the invariant it pins, because the failure mode here is never a
wrong value - it is a page that stops existing or a message that vanishes.
"""

from __future__ import annotations

import json

import pytest

from .conftest import Server, open_app

pytestmark = pytest.mark.e2e


def token_cookie(context, server: Server) -> str:
    """The cookie the server actually uses (its name is the app's business)."""
    for cookie in context.cookies(server.base):
        if cookie["name"].startswith("uh_token"):
            return cookie["name"]
    raise AssertionError("no uh_token cookie: the page did not authenticate")


def test_e2_a_stale_token_never_erases_the_page(browser, server: Server) -> None:
    """INVARIANT: a 401 explains itself; it never replaces the document.

    The token is random per process and the cookie is host-scoped: two instances on
    127.0.0.1:8765 and :8766 overwrite each other's cookie, and the older tab then
    talks with a dead token. What it must not do is delete the interface.
    """
    context, page = open_app(browser, server)
    name = token_cookie(context, server)

    context.add_cookies(
        [{"name": name, "value": "token-of-another-instance", "url": server.base + "/"}]
    )
    page.fill("#input", "bonjour, tu es la ?")
    page.press("#input", "Enter")
    page.wait_for_timeout(2500)

    assert page.locator("#input").count() == 1, "the composer must survive a stale token"
    assert page.locator("#messages").count() == 1, "the transcript must survive"
    assert page.input_value("#input") == "bonjour, tu es la ?", "the draft must be kept"
    body = page.inner_text("body")
    assert "Session expired" not in body or page.locator("#version-warning").is_visible()


def test_e1_a_hung_turn_never_swallows_the_next_message(browser, server: Server) -> None:
    """INVARIANT: the composer never loses text silently, and never lies.

    A turn whose response never arrives leaves `state.streaming` true. The second
    message must then be REFUSED with something visible - or sent after the first
    is abandoned - and the text must stay in the field.
    """
    context, page = open_app(browser, server)
    requests: list[str] = []

    def hang(route):
        requests.append(route.request.post_data or "")
        # never fulfil: the SSE response never starts, like a tool that never returns
        del route

    page.route("**/api/sessions/*/chat", hang)

    page.fill("#input", "premier message")
    page.press("#input", "Enter")
    page.wait_for_timeout(1500)
    assert len(requests) == 1

    page.fill("#input", "deuxieme message")
    page.press("#input", "Enter")
    page.wait_for_timeout(1500)

    kept = page.input_value("#input")
    transcript = page.inner_text("#messages")
    sent_twice = len(requests) == 2
    refused_visibly = "deuxieme message" not in transcript and kept == "deuxieme message"
    stuck = "explicitly refusing"  # the assertion message below spells out the failure
    assert sent_twice or refused_visibly, (
        "the second message vanished: "
        f"requests={len(requests)} input={kept!r} transcript={transcript[-120:]!r} ({stuck})"
    )


def test_e3_two_instances_do_not_kill_each_other(browser, make_server) -> None:
    """INVARIANT: two `uh web` on one host coexist in one browser profile.

    The cookie is host-scoped, not port-scoped: the second instance used to take the
    slot of the first, which then answered 401 to everything and lost its page.
    """
    first = make_server("first")
    context, page = open_app(browser, first)

    second = make_server("second")
    probe = context.new_page()
    probe.goto(second.url, wait_until="domcontentloaded")
    probe.wait_for_selector("#input", timeout=30000)
    probe.wait_for_timeout(1000)
    probe.close()

    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    assert page.locator("#input").count() == 1, "the first tab lost its interface"

    status = page.evaluate("async () => (await fetch('/api/status')).status")
    assert status == 200, json.dumps({"first_http_status": status})
