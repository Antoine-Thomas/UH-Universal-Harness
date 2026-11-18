"""P1-12 in a real browser: nothing in the thread, and a >200-message session stays quiet.

The failure this pins, measured before the fix: ``reconcileTranscript`` read the
store with a **fixed 200-row window** and compared it against the whole thread, so
a conversation past 200 messages reported a gap on every turn (Codex review on
PR #22) — and it said so with a red card inserted between two messages.

Two situations are exercised against the same session: an ordinary turn on a
250-message conversation (silence), then a store that genuinely holds fewer rows
than the screen (the header control, which refreshes and then disappears).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from uh.store import Store

from .conftest import open_app

pytestmark = pytest.mark.e2e

SEEDED = 250
#: Enough to leave the store genuinely smaller than the thread (190 < 204).
DELETED = 60

#: What the server streams for a one-word answer. The turn is routed at the network
#: layer, so the client runs its real path (composer, SSE parsing, `done`,
#: reconciliation) with no model, key or GPU — the trick `test_ux_invariants.py` uses.
SSE = (
    "event: open\ndata: {}\n\n"
    'event: token\ndata: {"text": "reponse du modele"}\n\n'
    "event: done\ndata: {}\n\n"
)


def seed(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True)
    with Store(home / "uh.db") as store:
        session_id = store.create_session(title="longue", model="stub:1b", provider="ollama")
        for index in range(SEEDED):
            role = "user" if index % 2 == 0 else "assistant"
            store.append_message(session_id, {"role": role, "content": f"message {index}"})
    return session_id


def run_one_turn(page) -> None:
    """One full turn through the composer, answered by the canned stream."""
    page.fill("#input", "trace la courbe")
    page.press("#input", "Enter")
    page.wait_for_timeout(2500)


def test_a_long_session_is_silent_and_a_real_gap_uses_the_header_control(
    make_server, browser, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    session_id = seed(home)
    server = make_server("home")
    context, page = open_app(browser, server)
    page.route(
        "**/api/sessions/*/chat",
        lambda route: route.fulfill(
            status=200, headers={"Content-Type": "text/event-stream"}, body=SSE
        ),
    )
    try:
        page.wait_for_function(
            "() => document.querySelectorAll('#messages .msg').length >= 200", timeout=20000
        )

        # 1. The Codex case: 250 messages stored, 200 loaded, one turn appends bubbles
        #    past that window. The window has to grow with the thread: no notice, and
        #    above all nothing drawn inside the conversation.
        run_one_turn(page)
        assert page.locator("#messages .msg").count() >= 202
        assert page.locator("#view-stale").is_hidden(), "un tour normal ne doit rien signaler"
        assert page.locator(".msg.failed").count() == 0, "aucun encart rouge dans le fil"
        assert "moins de messages" not in page.inner_text("#messages")

        # 2. A real gap: the store holds fewer rows than the screen, inside a window
        #    that still covers it. The control appears, outside the thread.
        with sqlite3.connect(home / "uh.db") as conn:
            conn.execute(
                "delete from messages where id in (select id from messages where session_id = ? "
                "order by id desc limit ?)",
                (session_id, DELETED),
            )
            conn.commit()
        run_one_turn(page)
        control = page.locator("#view-stale")
        control.wait_for(state="visible", timeout=15000)
        assert control.inner_text() == "Actualiser la vue"
        assert "moins de messages" not in page.inner_text("#messages")
        assert page.locator(".msg.failed").count() == 0

        # 3. The control does what its label says: it refreshes the view.
        control.click()
        page.wait_for_timeout(2000)
        assert page.locator("#view-stale").is_hidden(), "apres rafraichissement, plus d'etat perime"
        assert page.locator("#messages .msg").count() >= 150, "aucun message n'a ete efface"
    finally:
        context.close()
