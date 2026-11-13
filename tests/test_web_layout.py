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


@KEEP
def test_the_memory_actions_go_through_uh() -> None:
    """The browser never speaks to UHB: CSP is 'self', and the token stays server-side."""
    script = APP.read_text(encoding="utf-8")

    assert "/api/memory/rag/reload" in script
    assert "/api/memory/siyuan/blocks" in script
    assert "127.0.0.1:8101" not in script, "a direct UHB URL in the page would bypass UH"
    for line in script.splitlines():
        if "siyuan/blocks" in line:
            assert "/api/memory/siyuan/blocks" in line, f"unnamed route: {line.strip()}"
    assert "rag/status" not in script, "UH reports the RAG state; the page does not call UHB"


@KEEP
def test_the_memory_tab_says_reloaded_never_reindexed() -> None:
    """POST /rag/reindex reloads the index; claiming a rebuild would be a lie."""
    script = APP.read_text(encoding="utf-8").lower()

    assert "reload rag" in script
    assert "reloaded at" in script
    assert "reindexed" not in script
    assert "index rebuilt" not in script
    assert "rebuilt" not in script


# --- P6: the splash, the framed window, the dreams mount -------------------- #
DREAMS = ROOT / "uh" / "web" / "static" / "dreams.js"


@KEEP
def test_the_dreams_script_carries_its_own_helpers() -> None:
    """app.js is an ES module, so `el` and `api` are not on `window`.

    dreams.js is a classic script. Reaching for app.js's helpers threw
    `el is not defined` inside mountDreams, which catches and warns - so the
    "Rêves" button and the idea badge never appeared and nothing else failed.
    """
    script = DREAMS.read_text(encoding="utf-8")

    assert "function dreamsEl(" in script
    assert "async function dreamsApi(" in script
    for helper in ("el", "api"):
        assert not re.search(rf"(?<![\w$]){helper}\(", script), (
            f"dreams.js calls {helper}() from a module scope it cannot see"
        )


@KEEP
def test_the_dreams_panel_offers_the_three_outcomes() -> None:
    """Erase, keep, or fulfil - and a daemon toggle that is actually wired."""
    script = DREAMS.read_text(encoding="utf-8")

    for action in ("delete", "archive", "fulfill"):
        assert f'data-act="{action}"' in script, f"missing the {action} button"
    assert 'querySelector("#dreams-toggle").addEventListener("click", toggleDaemon)' in script
    assert "/api/dreams/daemon/" in script
    assert "waitForDaemon" in script, "a start that is not polled leaves a stale state line"


@KEEP
def test_the_splash_is_static_markup_and_the_script_ends_it() -> None:
    """The first paint must show something without waiting for the module."""
    page = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="splash"' in page
    assert 'class="splash-mark"' in page
    assert "dismissSplash" in script
    assert "splash.remove()" in script, "the overlay must leave the DOM, not only fade"
    assert "splash-pulse" in css
    assert ".splash.is-done" in css


@KEEP
def test_the_telemetry_tab_reads_the_route_without_a_chart_library() -> None:
    """The chart is SVG built from the payload.

    The CSP is 'self', so a CDN script would be blocked, and a vendored charting
    library is a dependency UH does not need to place seven rectangles.
    """
    script = APP.read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")

    assert 'id="tab-telemetry"' in page
    assert 'id="telemetry-panel"' in page
    assert 'id="telemetry-view"' in page
    assert "/api/telemetry" in script
    assert "createElementNS" in script, "the chart is built as SVG"
    assert "cdn." not in script.lower()
    assert "startTelemetryPolling" in script
    assert "stopTelemetryPolling" in script, "a hidden panel must not keep polling"


@KEEP
def test_the_second_brain_draws_the_graph_and_a_turn_can_carry_a_skill() -> None:
    """The graph and the skill badge are the two promises of P6/P8 in the UI."""
    script = APP.read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")

    assert 'id="memory-graph"' in page
    assert "/api/memory/graph" in script
    assert "memoryGraph(" in script
    assert 'id="skill-open"' in page
    assert 'id="skill-badge"' in page
    assert "skill: state.skill" in script, "the turn must name the skill it carries"
    # No diagramming library in the markup: the SVG is drawn from the payload.
    assert "mermaid" not in page.lower()
    assert "/static/mermaid" not in script.lower()


@KEEP
def test_the_data_tab_wires_backup_and_skills() -> None:
    """A panel that promises an export must call the routes that do it."""
    script = APP.read_text(encoding="utf-8")
    page = INDEX.read_text(encoding="utf-8")

    assert 'id="tab-data"' in page
    assert 'id="data-panel"' in page
    assert 'id="data-open"' in page
    for route in (
        "/api/backup/export",
        "/api/backup/import",
        "/api/backup/download/",
        "/api/skills",
        "/api/tools/status",
    ):
        assert route in script, f"the data panel must call {route}"
    # The archive is a file on disk handed over by the server: no data: URL, no
    # blob assembled in the browser, nothing kept in localStorage.
    section = script.split("// --- data (P8)")[1].split("// -------------------------------------------------------------------- boot --")[0]
    assert "localStorage." not in section and "localStorage[" not in section
    assert "showSaveFilePicker" not in section


@KEEP
def test_the_shell_reads_as_a_window_and_a_phone_gets_none() -> None:
    css = CSS.read_text(encoding="utf-8")
    layout = css.split(".layout {", 1)[1].split("}", 1)[0]
    mobile = css.split("@media (max-width: 768px)", 1)[1]

    assert "padding: 12px" in css.split(".layout {", 1)[0], "the frame needs a gutter"
    assert "border-radius" in layout
    assert "box-shadow" in layout
    assert "overflow: hidden" in layout, "the rounded corners have to clip the columns"
    assert "body { padding: 0; }" in mobile, "a phone is full bleed"
    assert ".layout { border: 0; border-radius: 0; box-shadow: none; }" in mobile
