"""The layout contract: three breakpoints, touch targets, one column on a phone.

There is no browser in the suite, so this checks what can be checked without
one - the stylesheet and the script agree on the breakpoints, the touch rules
live inside the mobile block, and the drawer has a handle. A wrong profile in a
screenshot is a judgement call; a breakpoint that exists in CSS and 1100 in JS
is a bug, and this catches it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = ROOT / "uh" / "web" / "static" / "style.css"
APP = ROOT / "uh" / "web" / "static" / "app.js"
INDEX = ROOT / "uh" / "web" / "static" / "index.html"

KEEP = pytest.mark.skipif(not CSS.is_file(), reason="web assets absent")


@KEEP
def test_the_three_breakpoints_are_declared() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert "@media (max-width: 1200px)" in css, "the panel goes away on a 13-inch laptop"
    assert "@media (max-width: 768px)" in css, "one column on a phone"


@KEEP
def test_the_script_uses_the_same_breakpoint_as_the_stylesheet() -> None:
    """isNarrow decides the overlay behaviour; if it disagrees, clicks misfire."""
    script = APP.read_text(encoding="utf-8")
    match = re.search(r'matchMedia\("\(max-width: (\d+)px\)"\)', script)

    assert match, "isNarrow must read a breakpoint"
    assert match.group(1) == "1200", "must match the CSS breakpoint"


@KEEP
def test_a_phone_gets_one_column() -> None:
    css = CSS.read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 768px)", 1)[1]

    assert "grid-template-columns: 1fr" in mobile
    assert ".splitter { display: none; }" in mobile, "no drag handles on a phone"


@KEEP
def test_the_right_panel_becomes_an_overlay_before_the_phone_block() -> None:
    css = CSS.read_text(encoding="utf-8")
    laptop = css.split("@media (max-width: 1200px)", 1)[1].split("@media (max-width: 768px)", 1)[0]

    assert "position: fixed" in laptop
    assert "#splitter-right { display: none; }" in laptop


@KEEP
def test_touch_targets_are_forty_four_pixels_inside_the_phone_block() -> None:
    css = CSS.read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 768px)", 1)[1]

    assert "min-width: 44px" in mobile
    assert "min-height: 44px" in mobile
    assert "font-size: 16px" in mobile, "iOS zooms any input under 16px"


@KEEP
def test_session_actions_are_always_visible_on_touch() -> None:
    css = CSS.read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 768px)", 1)[1]

    assert ".session-actions { opacity: 1; }" in mobile, "there is no hover on a phone"


@KEEP
def test_the_drawer_has_a_handle_and_the_banner_exists() -> None:
    page = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'id="panel-toggle"' in page
    assert "#panel-toggle { display: none; }" in css, "hidden on a desktop"
    assert 'id="remote-banner"' in page


@KEEP
def test_the_remote_banner_is_wired_to_the_capabilities_endpoint() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "showRemoteBanner" in script
    assert "api/system/capabilities" in script
    assert 'data.tier !== "small" && data.tier !== "remote"' in script


@KEEP
def test_the_memory_tab_wires_the_read_only_routes() -> None:
    """A tab that calls a route nobody wrote is a blank panel; this pins the wiring."""
    script = APP.read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")

    assert 'id="tab-memory"' in page
    assert 'id="memory-panel"' in page
    for route in (
        "api/memory/sources",
        "api/memory/wiki",
        "api/memory/search",
        "api/uhb/status",
    ):
        assert route in script, f"the Memory tab must call {route}"


@KEEP
def test_the_memory_tab_never_promises_a_button_it_cannot_honour() -> None:
    """RAG has no route and SiYuan has no client: the tab says so, it does not act."""
    script = APP.read_text(encoding="utf-8")

    assert "RAG not built yet" in script
    assert "README.md" in script
    assert "memory_write" in script, "MEMORY.md absence explains how it appears"
    assert "reindex" not in script.lower(), "no fake reindex button"


@KEEP
def test_the_decision_tab_reads_the_recorded_payload() -> None:
    script = APP.read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")

    assert 'id="tab-decision"' in page
    assert 'id="decision-panel"' in page
    assert "/decision`" in script, "the tab must ask the session, not the provider"

    # "complexity" may only appear bound to data: the turn's payload, or the
    # answer of /api/uhb/route. A hardcoded label would survive UHB being off
    # and would be a lie.
    allowed = ("prompt.complexity", "plan.complexity")
    occurrences = [line.strip() for line in script.splitlines() if "complexity" in line]
    assert occurrences, "the tab must show the classified prompt"
    for line in occurrences:
        assert any(binding in line for binding in allowed), f"unbound complexity: {line}"


@KEEP
def test_the_decision_tab_says_when_uhb_was_not_consulted() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "UHB was not consulted" in script
    assert "not applied to the turn" in script, "the on-demand plan must be labelled as a plan"
