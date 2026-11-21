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
def test_the_splash_stays_long_enough_to_be_seen() -> None:
    """A splash dismissed in 120 ms is a white flash, not a first impression."""
    script = APP.read_text(encoding="utf-8")
    match = re.search(r"SPLASH_MIN_MS = (\d+)", script)

    assert match, "the splash needs a minimum visible duration"
    assert int(match.group(1)) >= 500, "below ~500 ms nobody reads the mark"
    assert "setTimeout(dismissSplash" in script, "the minimum is a second look, not a busy loop"
    assert int(match.group(1)) < 4000, "the minimum must stay below the hard cap"


@KEEP
def test_the_dream_button_is_wired_to_a_real_route() -> None:
    """The accent button must call the endpoint that exists, not a hopeful path."""
    dreams = (ROOT / "uh" / "web" / "static" / "dreams.js").read_text(encoding="utf-8")
    api = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")

    assert '"/api/dreams/run"' in dreams
    assert "/api/dreams/run" in api, "a route the UI calls must be documented"
    assert "dreams-run" in dreams and "Faire un rêve maintenant" in dreams


@KEEP
def test_the_second_brain_panel_offers_a_button_not_a_shell_command() -> None:
    """An error message that tells the user to run a command is a bug in the UI."""
    script = APP.read_text(encoding="utf-8")

    assert "Run 'uh web'" not in script, "no shell instruction in a browser panel"
    assert "Connecter le second cerveau" in script
    assert '"/api/uhb/start"' in script and '"/api/uhb/stop"' in script
    assert "Arrêter le second cerveau" in script


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
def test_each_console_can_live_in_its_own_window() -> None:
    """Docking v2: any configuration view can be a floating window, and the layout
    is remembered per browser (it is furniture, not data)."""
    page = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 768px)", 1)[1]

    assert 'id="pane-detach"' in page
    assert 'id="pane-reset"' in page
    for name in ("detachPane", "dockPane", "resetPanes", "magneticRect", "restorePanes"):
        assert name in script, f"{name} missing from the pane layer"
    assert "uh.panes.v1" in script, "the layout key"
    assert "localStorage.setItem(PANE_KEY" in script
    assert ".pane-window" in css
    assert "#pane-detach, #pane-reset { display: none; }" in mobile, "no docking on a phone"
    # No vendored layout library: measured in the roadmap, and a bundle that
    # injects <style> would not render under this CSP.
    assert "vendor/dockview" not in page.lower()
    assert "vendor/dockview" not in script.lower()


@KEEP
def test_the_conversation_settings_panel_is_drawn_from_the_route() -> None:
    """A slider that promises a range the backend refuses is worse than no slider."""
    page = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="llm-open"' in page
    assert 'id="llm-badge"' in page
    assert "/api/llm/settings" in script
    assert "renderLlmRows" in script, "the rows come from the spec, not from markup"
    assert "state.llm.enabled" in script, "the master switch decides what is sent"
    assert "settings: state.llm" in script, "every turn carries the settings"


@KEEP
def test_the_panel_can_be_detached_and_a_phone_cannot() -> None:
    """Docking v1: detach the configuration panel, snap it to an edge, dock back."""
    page = INDEX.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    mobile = css.split("@media (max-width: 768px)", 1)[1]

    assert 'id="panel-float"' in page
    assert 'id="panel-dock"' in page
    assert "detachPanel" in script and "dockPanel" in script
    assert "DOCK_SNAP" in script, "the magnetic edges need a threshold"
    assert ".rightbar.floating" in css
    assert "#layout.dock-float" in css
    assert "#panel-float { display: none; }" in mobile, "no docking on a phone"
    assert "isPhone()" in script, "the same breakpoint guards the click"


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
    section = script.split("// --- data (P8)", 1)[1]
    # End at the next section banner, whatever it is: hard-coding "boot" here made
    # this test fail the day a new section was inserted before it.
    section = section.split("\n// --- ", 1)[0]
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


@KEEP
def test_a_finished_turn_never_rebuilds_the_thread() -> None:
    """A redraw from the store after every turn is how an answer vanished.

    The store is written by the turn's worker thread, so right after `done` it can
    still be one message behind the DOM. Rebuilding the thread from it erased the
    reply the user had just read; the turn now reconciles in place instead.
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split("async function sendMessage(text) {", 1)[1].split(
        "async function stopTurn()", 1
    )[0]

    assert "renderHistory(" not in body, "sendMessage must not redraw from the store"
    assert "reconcileTranscript(" in body, "the turn ends by reconciling, not redrawing"
    assert "assistant.remove()" not in body, "a failed turn keeps its bubble"
    assert "assistant.lastChild" not in body, "the content node is held, not guessed"


@KEEP
def test_the_reconciliation_never_removes_a_message() -> None:
    """Fewer rows in the store than on screen is a lag, not a deletion order."""
    script = APP.read_text(encoding="utf-8")
    body = script.split("async function reconcileTranscript() {", 1)[1].split(
        "\nasync function ", 1
    )[0]

    assert ".remove()" not in body
    assert 'textContent = ""' not in body
    assert "stored.length < boxes.length" in body, "a short store must be detected"
    assert "dataset.messageId" in body, "the ids are what the store is read for"


@KEEP
def test_every_form_field_can_be_autofilled() -> None:
    """A field with neither id nor name is what the browser flags as unlabelled.

    The configuration panel builds its inputs in JS, so the check covers the
    markup and the `el("input"|"select"|"textarea", ...)` calls alike - and names
    the offending line, because "somewhere in 5 000 lines" is not actionable.
    """
    offenders: list[str] = []

    html = INDEX.read_text(encoding="utf-8")
    for match in re.finditer(r"<(input|select|textarea)\b([^>]*)>", html, re.S):
        attrs = match.group(2)
        if "id=" not in attrs and "name=" not in attrs:
            offenders.append(f"index.html:{html[: match.start()].count(chr(10)) + 1}")

    script = APP.read_text(encoding="utf-8")
    for match in re.finditer(r'el\("(input|select|textarea)"', script):
        # The element's own setup follows the call: that is where an id lands.
        setup = script[match.end() :][:400]
        if ".id =" not in setup and ".name =" not in setup:
            offenders.append(f"app.js:{script[: match.start()].count(chr(10)) + 1}")

    assert not offenders, "form fields without an id or a name: " + ", ".join(offenders)


@KEEP
def test_the_columns_follow_the_window_until_the_user_drags() -> None:
    """Fixed 260/340 px columns is the "hard frame": a laptop loses its chat.

    The splitter still writes an explicit width when it is dragged - that part is
    the user's choice - but the default has to come from the stylesheet clamp.
    """
    css = CSS.read_text(encoding="utf-8")
    grid = css.split(".layout {", 1)[1].split("}", 1)[0]
    assert "clamp(" in grid, "the default column widths must be fluid"
    assert "var(--sidebar-w, 260px)" not in css
    assert "var(--right-w, 340px)" not in css

    script = APP.read_text(encoding="utf-8")
    defaults = re.search(r"const LAYOUT_DEFAULTS = \{([^}]*)\}", script)
    assert defaults, "layout defaults must stay declarative"
    assert "sidebar: null" in defaults.group(1), "no width until the splitter is dragged"
    assert "removeProperty" in script, "clearing a drag must hand the column back to CSS"


@KEEP
def test_every_panel_tab_loads_its_own_content() -> None:
    """A tab that only uncovers an empty section is a button that lies.

    "Fournisseurs" was the case: the list was loaded by the ⚙ button only, so
    clicking the tab showed a blank panel - reported as "the tab is empty".
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split("function showPanelTab(name) {", 1)[1].split("\nfunction ", 1)[0]
    html = INDEX.read_text(encoding="utf-8")

    assert 'name === "keys"' in body, "the Fournisseurs tab must load the providers"
    assert "openProviders()" in body, "one door: the same loader as the ⚙ button"

    # Every view the tab switch names must exist in the markup: a tab bound to a
    # missing section would fail silently on click.
    views = re.findall(r'"([a-z]+-view)"', body)
    assert len(views) >= 8, f"tab/view pairs look truncated: {views}"
    for view in views:
        assert f'id="{view}"' in html, f"{view} is switched but absent from index.html"


@KEEP
def test_the_model_choice_is_remembered_by_the_browser_too() -> None:
    """A restart that forgets the model is the bug; two memories are the fix.

    config.toml carries the choice, and the browser keeps the same pair: when the
    file refuses the write (a hand-edited config), the page still restores what
    the user picked instead of showing the first local model.
    """
    script = APP.read_text(encoding="utf-8")

    assert 'const MODEL_CHOICE_KEY = "uh.selected_model"' in script
    assert "storeModelChoice(" in script, "the switch must write the pair"
    assert "storedModelChoice()" in script, "and the boot must read it back"
    assert "state.modelRestored" in script, "one restore attempt, never a loop"
    assert "result.persisted === false" in script, "a refused config write is shown"


@KEEP
def test_the_providers_panel_is_loaded_once_and_stays_isolated() -> None:
    """Three entry points, one request, and a neighbour that cannot empty the list.

    Measured in a real browser: `?panel=keys` asked `/api/providers` twice (the
    URL opener then the tab), and a 500 on `/api/router/settings` wiped a list
    that had already rendered. The loader is now coalesced and the router
    settings load beside it, not inside it.
    """
    script = APP.read_text(encoding="utf-8")
    loader = script.split("function loadProviders() {", 1)[1].split("\n// Remote access", 1)[0]

    assert "if (state.providersPromise) return state.providersPromise" in loader, (
        "one in-flight promise, shared by every entry point"
    )
    assert "state.providersPromise = run" in loader
    assert 'setAttribute("aria-busy"' in loader, "a loading state the interface can observe"
    assert "loadRouterSettings().catch(" in loader, (
        "the router settings must not be able to fail the provider list"
    )
    assert "renderRouterSettings(await api(" not in loader, (
        "that ordering is the wipe: settings inside the list's chain"
    )
    settings = script.split("async function loadRouterSettings() {", 1)[1].split("\n}\n", 1)[0]
    assert "try {" in settings and "catch (error)" in settings, (
        "the settings load owns its failure, in its own box"
    )


@KEEP
def test_the_transcript_reconciliation_ignores_system_bubbles() -> None:
    """Notices are shown in the transcript but never stored, so counting them made
    the DOM look ahead of the store: any turn ending with a notice produced a
    false "Le transcript enregistré contient moins de messages que l'écran".

    Since P1-12 the two sides are built by ``threadBubbles()`` and
    ``storedMessages()`` — the filter has to hold in both, and the reconcile body
    must keep using them.
    """
    script = APP.read_text(encoding="utf-8")
    bubbles = script.split("function threadBubbles() {", 1)[1].split("\n}\n", 1)[0]
    stored = script.split("async function storedMessages(count) {", 1)[1].split("\n}\n", 1)[0]
    body = script.split("async function reconcileTranscript() {", 1)[1].split(
        "\nasync function ", 1
    )[0]

    assert 'classList.contains("system")' in bubbles, "system bubbles are not messages"
    assert 'classList.contains("failed")' in bubbles, (
        "the client-drawn error bubble is not one either"
    )
    assert '!== "system"' in stored, "stored system rows are excluded on both sides"
    assert "threadBubbles()" in body, "the count comes from the filtered bubbles"
    assert "storedMessages(wanted)" in body, "the store side comes from the filtered read"
    assert "const stored = data.messages || [];" not in script, (
        "that is the version that counted the interface's own notices"
    )


@KEEP
def test_the_transcript_notice_lives_in_the_header_not_in_the_thread() -> None:
    """P1-12: a red card between two messages read as data loss.

    A desynchronised view is a state: it shows up as a discreet control in the
    header ("Actualiser la vue"), which refreshes the view and nothing else — it
    never draws a bubble in the conversation, and it never touches the store.
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split("async function reconcileTranscript() {", 1)[1].split(
        "\nasync function ", 1
    )[0]
    markup = INDEX.read_text(encoding="utf-8")
    style = CSS.read_text(encoding="utf-8")

    assert "appendMessage(" not in body, "rien ne doit être dessiné dans le fil"
    assert "notice-action" not in body, "l'encart rouge a quitté la conversation"
    assert "viewStale(desynced);" in body, "l'état périmé passe par le contrôle d'en-tête"
    assert 'id="view-stale"' in markup and "Actualiser la vue" in markup
    assert ".view-stale[hidden]" in style, "un contrôle caché doit vraiment disparaître"
    assert "openSession(state.sessionId)" in script.split("function wireViewStale()", 1)[1], (
        "le bouton recharge la session qu'il nomme"
    )


@KEEP
def test_a_command_failure_is_not_labelled_a_model_failure() -> None:
    """`/web` died on a dropped connection; the bubble said "the model failed".

    The frame carries `source: "command"` and the browser says so, instead of
    sending people to check a provider key for a dead search endpoint.
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split('} else if (event === "error") {', 1)[1].split("} else if", 1)[0]

    assert 'data.source === "command"' in body
    assert "Commande" in body


@KEEP
def test_a_cut_answer_offers_to_continue() -> None:
    """A provider that dies mid-stream used to leave words on screen with no frame.

    They looked like a finished answer, and a reload lost them. The frame now
    says `partial: true` and the bubble carries one button.
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split('} else if (event === "error") {', 1)[1].split("} else if", 1)[0]

    assert "data.partial" in body, "the browser is told whether the words are real"
    assert 'el("button"' in body and "Continuer" in body
    assert "sendMessage(" in body, "continuing is a message, not hidden magic"


@KEEP
def test_the_decision_view_follows_the_session_that_is_open() -> None:
    """A tab opened from a deep link loads before any session exists.

    Its loader then ran with no id, and the panel kept "envoyez un message" for
    ever, even with a session full of recorded turns in front of it.
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split("async function openSession(id) {", 1)[1].split("\nasync function ", 1)[0]

    assert 'state.activeTab === "decision"' in body, "opening a session reloads the view"
    assert "loadDecision()" in body
    # And the two silences are told apart, in French.
    empty = script.split("if (!data.available) {", 1)[1].split("return;", 1)[0]
    assert "Aucun tour enregistré" in empty
    assert "Envoyez un message" in empty


@KEEP
def test_the_settings_master_switch_is_a_control_you_can_see() -> None:
    """It was a 13 px checkbox labelled "Paramètres personnalisés".

    An on/off nobody sees, on a panel that changes what is sent to a provider -
    the operator reported the button as missing.
    """
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert 'id="llm-enabled" class="llm-switch"' in script, "a button, not a hidden checkbox"
    assert 'role="switch"' in script and "aria-checked" in script
    assert "setLlmSwitch(" in script, "one function owns the three states"
    assert ".llm-switch.on" in css, "the on state is drawn, not implied"
    assert ".llm-switch:focus-visible" in css, "keyboard users see where they are"
    assert 'type="checkbox" id="llm-enabled"' not in script, "the invisible control is gone"


@KEEP
def test_the_tools_panel_shows_uhbs_catalogue_and_keeps_no_copy_of_it() -> None:
    """One catalogue, in UHB. A list of tool names here would be a second truth."""
    page = INDEX.read_text(encoding="utf-8")
    assert 'id="tab-tools"' in page and 'id="tools-view"' in page
    assert 'id="tools-list"' in page and "aria-busy" in page

    script = APP.read_text(encoding="utf-8")
    body = script.split("function catalogCard(entry) {", 1)[1].split("\nfunction showPanelTab(", 1)[
        0
    ]
    # The catalogue is data: no name of a real tool appears in this file.
    for name in ("execute_code", "read_file", "write_file", "search_files"):
        assert name not in script, f"{name} is hardcoded in the interface"
    assert "/api/tools/catalog" in body, "the panel reads UHB, it does not embed"
    assert "state.toolsPromise" in body, "one in-flight request per click, as for providers"
    assert "innerHTML" not in body, "artefacts and schemas are rendered as text, never as markup"
    assert "innerText" in body or "textContent" in body


@KEEP
def test_tool_artifacts_are_rendered_as_images_and_never_as_markup() -> None:
    """The rendering contract of P0-5, checked on the source, not on a screenshot.

    The bytes arrive in the tool payload; the interface may only point an `<img>`
    at a `data:` URL whose prefix matches an allow-listed MIME. Anything else is
    skipped, because the payload's MIME is a claim — and `svg` is not in the list.
    """
    script = APP.read_text(encoding="utf-8")
    body = script.split("function drawArtifacts(container, artifacts, context) {", 1)[1].split(
        "\nfunction fillToolCard(", 1
    )[0]

    assert "innerHTML" not in body, "a data URL is data; innerHTML would make it markup"
    assert 'const img = el("img"' in body and "img.src = url" in body
    assert "ARTIFACT_MIME.includes(mime)" in body, "the MIME is checked, not trusted"
    assert "!url.startsWith(`data:${mime};base64,`)" in body, "only data: is accepted"
    assert '"image/png", "image/jpeg", "image/gif", "image/webp"' in script
    assert "image/svg" not in body, "svg is a document with scripts, not a bitmap"
    # Wired: a replayed session renders its artefacts because fillToolCard runs.
    assert "renderArtifacts(card, content);" in script
    assert "drawArtifacts(box, message.artifacts)" in script, "a reload draws stored images"
    assert "drawArtifacts(assistant, data.images)" in script, "a live answer draws them too"


@KEEP
def test_the_providers_list_loads_on_arrival() -> None:
    """The column is open on a wide screen and "Fournisseurs" is the default tab.

    Measured 2026-11-19: the list stayed empty until another tab was clicked.
    `showPanelTab` was the only caller of the loader that fetches, and boot never
    called it - so the active tab was uncovered, never loaded.
    """
    script = APP.read_text(encoding="utf-8")
    boot = script.split("async function boot() {", 1)[1].split("\n// First launch", 1)[0]

    assert 'if (providersAreOpen() && state.activeTab === "keys") openProviders();' in boot, (
        "boot must load the default tab it opens"
    )
    # And the tab click path still loads it, with the same coalesced loader.
    tab = script.split("function showPanelTab(name) {", 1)[1].split("\nfunction ", 1)[0]
    assert 'if (name === "keys")' in tab and "openProviders();" in tab


@KEEP
def test_the_decision_view_exposes_the_execution_mode() -> None:
    """The assistant told the operator "go to Décision and switch to dev" - the
    panel had no control at all, and the mode is a property of the UHB process.

    Measured 2026-11-19: three models in a row refused with
    "mode 'chat' has no tool 'execute_code'", and nothing in the interface could
    change it. The control now lives in that panel, with human labels, and it
    calls the route that restarts the second brain.
    """
    script = APP.read_text(encoding="utf-8")

    assert "async function renderExecutionMode()" in script
    assert 'api("/api/uhb/mode"' in script
    # Labels are words a person uses, not the profile keys.
    # 19/11 : le libelle porte le nom francais ET l'identifiant technique.
    assert '"Discussion (chat)"' in script and '"Atelier (dev)"' in script
    assert "lecture seule" in script and "exécuter du code" in script
    # It is a sibling of the panel that renderDecision clears on every reload.
    assert "host.parentNode.insertBefore(card, host)" in script


@KEEP
def test_the_artifact_toolbar_speaks_french_with_icons() -> None:
    """The bar under an image was four text buttons, two of them emoji.

    Measured 2026-11-19 (screenshot): "Poubelle" beside a bare 👍/👎 pixel pair and
    a lowercase "garder" link, no way to keep a copy of the picture. Now: inline
    SVG icons (an emoji takes whatever size the platform decides), a short French
    label per button, an explicit accessible name, and a download link built from
    the artifact's own data URL.
    """
    script = APP.read_text(encoding="utf-8")

    for label in ("Garder", "Utile", "Pas utile", "Télécharger", "Corbeille"):
        assert f'"{label}"' in script, f"the toolbar offers {label}"
    assert "👍" not in script and "👎" not in script, "no emoji as a button face"
    assert "ARTIFACT_ICONS" in script and 'createElementNS("http://www.w3.org/2000/svg"' in script
    assert "link.download =" in script, "the image can actually be saved"
    assert 'setAttribute("aria-label", title)' in script, "every icon button is named"


@KEEP
def test_the_mode_is_visible_in_the_header_and_grantable_from_the_refusal() -> None:
    """Conditions of point 1: default chat, a permanent badge, a card that re-sends.

    The refusal used to say "switch to dev" and point at a panel without a control.
    Now the badge is in the header on every screen, and the refusal carries a card
    whose button grants execution and re-posts the turn the user already typed.
    """
    page = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    assert 'id="mode-badge"' in page and 'aria-label="Mode d\'exécution"' in page
    assert "function refreshModeBadge()" in script and 'api("/api/uhb/status")' in script
    assert "function toggleModeFromBadge()" in script
    # Human labels, not the profile keys.
    # 19/11 : l'operateur a demande que l'en-tete nomme le mode francais ET son
    # identifiant technique, « Atelier » seul laissant le modele parler de « mode dev ».
    assert "Discussion (chat) : lecture seule" in script
    # The confinement is measured (counter-probe of 19/11), so the badge describes
    # it: the workshop runs code, and writes stay in the run directory.
    assert "Atelier (dev) : exécute du code (dossier du run uniquement)" in script
    assert "portée non vérifiée" not in script, "the old caveat must not survive the proof"
    assert "peut exécuter du code et créer des fichiers" not in script, "no unproven promise"
    # The card answers the refusal event, and re-sends the same turn.
    assert 'event === "mode_refused"' in script
    assert "function modeRefusalCard(" in script
    assert "state.lastUserText" in script and "await sendMessage(again)" in script
    assert "Autoriser pour cette conversation" in script


@KEEP
def test_no_tool_can_change_the_execution_mode() -> None:
    """The model proposes tools; the operator grants capability.

    A mode change restarts the second brain. If any tool could call that route, the
    model would be able to grant itself execution - the one thing the gate exists to
    prevent. The check is structural: tool code lives in uh/toolsets, and none of it
    knows the route, nor does the loop.
    """
    for path in (ROOT / "uh" / "toolsets").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "/api/uhb/mode" not in text, f"{path.name} can reach the mode route"
        assert "uhb_set_mode" not in text and "uhb/mode" not in text
    agent = (ROOT / "uh" / "agent.py").read_text(encoding="utf-8")
    assert "/api/uhb/mode" not in agent, "the loop must not grant itself execution"


# --------------------------------------------------------------------------- #
# UI/UX and accessibility contract (no browser in the suite: the sources are checked)
# --------------------------------------------------------------------------- #


def _luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    if len(colour) == 3:
        colour = "".join(ch * 2 for ch in colour)
    channels = [int(colour[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _token(block: str, name: str) -> str:
    match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{3,6}})", block)
    assert match, f"{name} missing"
    return re.findall(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{3,6}})", block)[-1]


def _theme_blocks() -> tuple[str, str]:
    css = CSS.read_text(encoding="utf-8")
    root = re.findall(r":root\s*\{(.*?)\}", css, re.S)
    dark = re.findall(r'html\[data-theme="dark"\]\s*\{(.*?)\}', css, re.S)
    return "\n".join(root), "\n".join(dark)


@KEEP
def test_the_csp_stays_strict_in_the_static_files() -> None:
    """No inline style or handler, no CDN, no third-party host: the CSP forbids them."""
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert "<style" not in html and 'style="' not in html
    assert not re.search(r"\son[a-z]+=\"", html), "an inline handler is blocked by the CSP"
    assert not re.search(r'setAttribute\(\s*["\']style["\']', script)
    assert ".cssText" not in script
    assert not re.search(r"<[a-z]+[^>]*\sstyle=", script), "a style attribute built from JS"
    for text in (html, css):
        assert not re.search(r"https?://(?!www\.w3\.org)[^\s\"')]+\.(js|css|woff2?)", text)


@KEEP
def test_the_brand_colours_are_the_official_ones() -> None:
    css = CSS.read_text(encoding="utf-8")
    root, _dark = _theme_blocks()

    assert _token(root, "--brand-ink").lower() == "#284543"
    assert _token(root, "--brand-gold").lower() == "#fdc502"
    assert "--highlight: #fdc502" in css


@KEEP
def test_interactive_borders_and_text_reach_wcag_aa_in_both_themes() -> None:
    root, dark = _theme_blocks()
    light_page, light_panel = "#eef6f5", "#ffffff"
    dark_page, dark_panel = "#1c302e", "#284543"

    for surface in (light_page, light_panel):
        assert _contrast(_token(root, "--control-border"), surface) >= 3.0
        assert _contrast(_token(root, "--link"), surface) >= 4.5
        assert _contrast(_token(root, "--muted"), surface) >= 4.5
    for surface in (dark_page, dark_panel):
        assert _contrast(_token(dark, "--control-border"), surface) >= 3.0
        assert _contrast(_token(dark, "--link"), surface) >= 4.5
        assert _contrast(_token(dark, "--muted"), surface) >= 4.5
    assert _contrast(_token(root, "--on-accent"), _token(root, "--accent")) >= 4.5
    assert (
        _contrast(_token(dark, "--brand-gold"), dark_panel) >= 3.0
        if "--brand-gold" in dark
        else True
    )
    assert _contrast("#fdc502", "#284543") >= 4.5, "gold focus ring on the ink colour"


@KEEP
def test_every_css_variable_in_use_is_defined() -> None:
    """`var(--line)` and `var(--fg-dim)` once styled a picture border with nothing."""
    css = CSS.read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-zA-Z0-9-]+)\s*:", css))
    used = re.findall(r"var\((--[a-zA-Z0-9-]+)(?!\s*,)\s*\)", css)
    missing = sorted({name for name in used if name not in defined})

    assert not missing, f"undefined CSS variables: {missing}"


@KEEP
def test_booleans_are_switches_and_the_decision_panel_speaks_french() -> None:
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    rows = script[
        script.index("function renderLlmRows") : script.index("async function loadLlmPanel")
    ]
    assert 'type = "checkbox"' not in rows, "a bare 13 px checkbox for a setting"
    assert 'setAttribute("role", "switch")' in rows and "aria-checked" in rows
    assert re.search(r"\.llm-switch\s*\{[^}]*min-height:\s*34px", css)

    decision = script[
        script.index("function renderDecision") : script.index("// A plan UHB would give now")
    ]
    # The raw wording survives only as a tooltip (`.title`), never as visible text.
    for jargon in ("no uhb_route call", "no chain", "decisionBlock('Prompt'"):
        for line in decision.splitlines():
            if jargon in line:
                assert ".title" in line or line.strip().startswith("//"), line
    assert "Détails techniques" in decision, "the metrics belong in a collapsible detail"
    assert "Le second cerveau (UHB) n'a pas été consulté" in decision


@KEEP
def test_no_raw_http_code_is_shown_and_errors_offer_an_action() -> None:
    script = APP.read_text(encoding="utf-8")

    assert "`HTTP ${" not in script, "a raw status code must go through httpErrorText"
    assert "function humanizeError" in script and "function decorateError" in script
    for label in ("Nouvelle session", "Autoriser l'outil", "Recharger"):
        assert f'"{label}"' in script, label
    assert "WinError" in script, "the Windows socket errors are translated too"


@KEEP
def test_tool_cards_have_four_distinct_states() -> None:
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    for name in ("loading", "success", "refused", "error"):
        assert f'[data-state="{name}"]' in css, name
        assert f"{name}:" in script[script.index("const TOOL_STATE_TEXT") :][:300], name
    assert 'aria-busy", String(toolState === "loading")' in script
    assert "TOOL_SLOW_MS = 3000" in script, "a text hint after three seconds"
    assert "Changer de mode" in script and "Mode lecture seule (chat)" in script


@KEEP
def test_image_artifacts_are_bounded_lazy_and_openable() -> None:
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    assert re.search(
        r"\.tool-artifact-img\s*\{[^}]*max-width:\s*100%[^}]*max-height:\s*320px", css, re.S
    )
    assert 'img.loading = "lazy"' in script
    assert "function artifactOpen" in script and "tool-artifact.loading" in css


@KEEP
def test_the_uhb_status_has_four_states_with_icon_and_words() -> None:
    script = APP.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")

    block = script[
        script.index("const UHB_STATES") : script.index("async function toggleModeFromBadge")
    ]
    for expected in ("Mode Dev", "Mode Chat", "Dégradé", "Hors ligne"):
        assert expected in block, expected
    assert block.count("icon:") == 4, "each state carries an icon, never a colour alone"
    for state in ("degraded", "offline"):
        assert f".mode-badge.{state}" in css
    assert "setInterval" in script[script.index("refreshModeBadge().catch") :][:400]


@KEEP
def test_system_notices_are_live_regions_and_modals_trap_the_focus() -> None:
    html = INDEX.read_text(encoding="utf-8")
    script = APP.read_text(encoding="utf-8")

    for ident in ("remote-banner", "terminal-warning", "version-warning", "model-note"):
        tag = re.search(rf'<div id="{ident}"[^>]*>|<span id="{ident}"[^>]*>', html)
        assert tag and ("aria-live" in tag.group(0) or 'role="status"' in tag.group(0)), ident
    assert '<html lang="fr"' in html
    assert "function installModalAccessibility" in script
    assert 'setAttribute("aria-modal", "true")' in script
    assert re.search(r":focus-visible\s*\{", CSS.read_text(encoding="utf-8"))
