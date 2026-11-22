"""P5-UI (22/11): the polish pass is a rendered behaviour, not a stylesheet diff.

Two things are measured here, both on the screen and not in a string of the source:
the confirmation every destructive gesture now uses is a real modal (Escape closes it
and does not also collapse the right column), and the settings tabs never clip their
label. The rest of the pass (contrast, font size, themes) is measured by
``scripts/prove_ui_cdp.py``, which is a proof, not a gate.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

from .conftest import Server, open_app  # noqa: E402


def test_the_quit_confirmation_is_a_modal_that_escape_closes(
    browser, server: Server
) -> None:
    """`window.confirm` blocked the page and could not be styled; the replacement is
    the same `.modal` as every other dialog, so Escape belongs to it - and only to it.
    """
    context, page = open_app(browser, server)
    try:
        width_before = page.eval_on_selector(
            "#layout", "el => Math.round(el.getBoundingClientRect().width)"
        )
        page.click("#quit-uh")
        page.wait_for_selector(".modal-backdrop .modal", timeout=10000)
        assert page.eval_on_selector(".modal-title", "el => el.textContent").startswith(
            "Quitter UH"
        ), "the confirmation names the gesture it is confirming"

        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.modal-backdrop')", timeout=5000)

        width_after = page.eval_on_selector(
            "#layout", "el => Math.round(el.getBoundingClientRect().width)"
        )
        assert width_after == width_before, (
            "Escape closed the dialog AND collapsed the right column: the key must stop "
            "at the modal"
        )
    finally:
        context.close()


def test_the_settings_tabs_never_clip_a_label(browser, server: Server) -> None:
    """A wrapped-but-truncated tab looks fine in the DOM and unreadable on screen."""
    context, page = open_app(browser, server)
    try:
        clipped = page.evaluate(
            """() => [...document.querySelectorAll('.panel-tab')]
                 .filter((node) => node.offsetParent !== null)
                 .filter((node) => node.scrollWidth > node.clientWidth + 1)
                 .map((node) => node.textContent.trim())"""
        )
        assert clipped == [], f"tabs clipped by their own width: {clipped}"
    finally:
        context.close()
