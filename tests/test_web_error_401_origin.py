"""A 401 in `uh web` has two meanings, and their two repairs must not swap.

UH's own ``/api`` routes answer 401 when the page's token is stale - reloading is the
fix. A model provider answers 401 *inside* a turn's SSE ``error`` frame when it refuses
the API key - reloading changes nothing, the key has to be checked. Both used to land
on the same sentence ("jeton périmé, rechargez la page"), which sent people to reload
a page that was fine and never to the key that was wrong.

The static checks below always run. The behavioural one drives the real functions in
node when it is installed (skipped otherwise, so the suite never depends on it).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "uh" / "web" / "static" / "app.js"

KEEP = pytest.mark.skipif(not APP.is_file(), reason="web assets absent")

#: The error helpers are self-contained: from the message table to ``errorButton``,
#: nothing touches the DOM, which is what makes them runnable outside a browser.
BLOCK_START = "const HTTP_TEXTS = {"
BLOCK_END = "function errorButton("


def _script() -> str:
    return APP.read_text(encoding="utf-8")


def _block() -> str:
    script = _script()
    assert BLOCK_START in script and BLOCK_END in script, "the error helpers moved"
    return script[script.index(BLOCK_START) : script.index(BLOCK_END)]


@KEEP
def test_a_provider_401_sends_people_to_the_key_not_to_a_reload() -> None:
    block = _block()
    table = block[block.index("const HTTP_TEXTS_PROVIDER") : block.index("function httpErrorText")]

    assert "401:" in table, "the provider table must cover the code that matters"
    assert "« Fournisseurs »" in table, "the key lives in one panel and it must be named"
    assert "Rechargez" not in table, "a reload cannot repair a key the provider refused"


@KEEP
def test_a_local_401_keeps_the_reload_advice() -> None:
    script = _script()
    literal = script[
        script.index("const HTTP_TEXTS = {") : script.index("const HTTP_TEXTS_PROVIDER")
    ]

    assert "401: \"Cette page n'est plus reliée à UH" in literal
    assert "Rechargez la page." in literal


@KEEP
def test_every_call_site_declares_whether_the_401_came_from_the_page_or_the_turn() -> None:
    script = _script()

    assert "humanizeError(failed);" not in script, (
        "an origin-less call cannot tell the two 401s apart"
    )
    assert 'humanizeError(failed, "provider")' in script, "the SSE error frame is the provider"
    assert 'humanizeError(failed, "ui")' in script, "the catch around the page's own fetch is UH"

    # And they are in the right places: the frame branch reports the provider, the
    # catch around `fetch('/api/.../chat')` reports the page.
    frame = script[script.index('event === "error"') : script.index('event === "answer"')]
    assert 'humanizeError(failed, "provider")' in frame

    # The catch that wraps `fetch('/api/sessions/.../chat')`, not the first one in
    # the file (small event handlers have catches of their own).
    catch_at = script.index('if (error.name !== "AbortError") {')
    catch = script[catch_at : script.index("} finally {", catch_at)]
    assert 'humanizeError(failed, "ui")' in catch


@KEEP
def test_the_provider_401_offers_a_button_that_opens_the_key_panel() -> None:
    script = _script()
    decorator = script[
        script.index("function decorateError(") : script.index("// --- cartes d'outils")
    ]

    assert 'action === "keys"' in decorator
    assert '"Vérifier la clé"' in decorator, "the repair is a button, not a sentence to retype"
    assert 'showPanelTab("keys")' in decorator


@KEEP
def test_humanize_error_tells_the_two_401s_apart_when_it_runs(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed: the static checks above still hold")

    harness = tmp_path / "humanize.js"
    harness.write_text(
        _block()
        + """
const cases = [
  ["provider", "DeepSeek HTTP 401: invalid api key"],
  ["ui", "jeton périmé : cette page n'est plus reliée à UH"],
  ["ui", "HTTP 401"],
  ["provider", "HTTP 422: conversation invalide"],
];
process.stdout.write(
  JSON.stringify(cases.map(([origin, raw]) => ({ origin, raw, ...humanizeError(raw, origin) })))
);
""",
        encoding="utf-8",
    )
    done = subprocess.run(  # noqa: S603 - a path resolved above, no shell
        [node, str(harness)],
        capture_output=True,
        check=True,
        encoding="utf-8",
        text=True,
        timeout=60,
    )
    rows = {(row["origin"], row["raw"]): row for row in json.loads(done.stdout)}

    provider = rows[("provider", "DeepSeek HTTP 401: invalid api key")]
    assert provider["actions"] == ["keys"]
    assert "« Fournisseurs »" in provider["text"]
    assert "Rechargez" not in provider["text"]
    assert "HTTP 401" not in provider["text"], (
        "the raw code stays in the details, never in the sentence"
    )

    stale = rows[("ui", "jeton périmé : cette page n'est plus reliée à UH")]
    assert stale["actions"] == ["reload"]
    assert "Rechargez la page" in stale["text"]

    local = rows[("ui", "HTTP 401")]
    assert local["actions"] == ["reload"]
    assert "jeton périmé" in local["text"]

    other = rows[("provider", "HTTP 422: conversation invalide")]
    assert other["actions"] == ["new", "reload"], (
        "422 is still about this conversation, not the key"
    )
