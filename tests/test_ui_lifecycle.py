"""The lifecycle invariants, checked without a browser.

The e2e tests (tests/e2e, marked ``e2e``) are the real proof; these keep the same
rules from regressing in every ordinary run, where no Chromium is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "uh" / "web" / "static" / "app.js"
INDEX = ROOT / "uh" / "web" / "static" / "index.html"
APP_PY = ROOT / "uh" / "web" / "app.py"
CLI = ROOT / "uh" / "cli.py"
UHB = ROOT / "uh" / "uhb_spawn.py"


def test_no_ui_code_erases_the_document() -> None:
    """INVARIANT: a 401, a failed boot or a network error never replaces the page.

    ``document.body.textContent = …`` was the one line that turned "the token of
    another instance took the cookie" into "the whole interface disappeared".
    """
    script = APP.read_text(encoding="utf-8")
    offenders = [
        f"app.js:{number}"
        for number, line in enumerate(script.splitlines(), 1)
        if "document.body.textContent" in line or "document.body.innerHTML" in line
    ]
    assert not offenders, "UI code clears the document: " + ", ".join(offenders)
    assert 'id="app-banner"' in INDEX.read_text(encoding="utf-8"), (
        "the non-destructive banner must exist for the message to land in"
    )


def test_one_submit_path_and_the_draft_survives() -> None:
    """INVARIANT: Enter and the button do the same thing, and the text is kept.

    The composer used to clear its field, then call sendMessage(), which returned
    immediately when a previous turn was still flagged as streaming: the message was
    neither sent nor kept.
    """
    script = APP.read_text(encoding="utf-8")
    assert "function submitComposer(" in script
    assert "function canSend(" in script
    assert "function restoreDraft(" in script
    # Both entry points go through the same guard.
    assert script.count("submitComposer()") >= 2
    # The old pattern - clear then send - is gone.
    assert 'input.value = "";\n      sendMessage(text);' not in script
    # The field is emptied on the server's "open" frame, not before the request.
    assert 'event === "open"' in script
    # A turn is refused visibly instead of being swallowed.
    assert "un tour est déjà en cours" in script


def test_the_token_outlives_a_restart_and_the_cookie_is_per_port() -> None:
    """INVARIANT: a restart does not invalidate every open tab.

    A per-process token meant every restart answered 401 to every tab; a shared
    cookie name meant two instances on 127.0.0.1 overwrote each other. Both are
    fixed in the same place, so both are pinned here.
    """
    source = APP_PY.read_text(encoding="utf-8")
    assert "def _stable_token(" in source
    assert 'TOKEN_FILE = "web-token"' in source
    assert 'app.state.cookie_name = f"{COOKIE_PREFIX}_{port}"' in source
    assert "rotate_token" in source
    # The cookie is read through the port-scoped name, never the bare constant.
    assert "request.cookies.get(COOKIE_NAME)" not in source


def test_a_chat_stream_always_has_a_life_sign_and_an_end() -> None:
    """INVARIANT: a turn that never returns still ends, visibly.

    An SSE stream with no heartbeat and no cap left ``state.streaming`` true for
    ever: the next message vanished, and "Quitter UH" answered 409.
    """
    source = APP_PY.read_text(encoding="utf-8")
    assert "HEARTBEAT_S" in source and "TURN_CAP_S" in source
    assert 'yield frame("ping"' in source
    script = APP.read_text(encoding="utf-8")
    assert "STALL_MS" in script and "UH_STALL_MS" in script


def test_one_instance_per_port() -> None:
    """INVARIANT: a second launch reports the first one instead of moving on."""
    source = APP_PY.read_text(encoding="utf-8")
    cli = CLI.read_text(encoding="utf-8")
    assert '"/healthz"' in source
    assert "_uh_version_on(" in source
    assert "sert déjà UH" in source
    assert "port_fallback=bool(getattr(args, \"port_fallback\", False))" in cli
    assert "--quit-existing" in cli and "--rotate-token" in cli


def test_an_orphan_uhb_can_be_stopped_by_its_pid_file(tmp_path, monkeypatch) -> None:
    """INVARIANT: "Quitter UH" also stops a UHB this process did not start.

    ``ensure()`` returns None when the port already answers, so the child was
    invisible; the pid file records who started it, and a recycled pid is checked
    against the command line before anything is killed.
    """
    from uh import uhb_spawn

    monkeypatch.setenv("UH_HOME", str(tmp_path))
    path = uhb_spawn.pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    # A pid that exists but is not a UHB (this test process): checking the command
    # line is what stops a recycled pid from costing an unrelated process.
    path.write_text('{"pid": %d, "url": "http://127.0.0.1:8101", "origin": "test"}' % __import__("os").getpid(), encoding="utf-8")

    assert uhb_spawn.stop_recorded(timeout=3) is False
    assert not path.exists(), "a stale record is cleaned up, not retried forever"

    source = UHB.read_text(encoding="utf-8")
    assert "def record_pid(" in source and "def stop_recorded(" in source
    assert "def stop_recorded_hint(" in source


def test_the_cleanup_documentation_spares_wazuh() -> None:
    """The cleanup command must never be "kill every pythonw"."""
    doc = ROOT / "docs" / "INSTALL.md"
    if not doc.is_file():
        pytest.skip("INSTALL.md absent")
    text = doc.read_text(encoding="utf-8")
    if "Stop-Process" in text:
        assert "wazuh" in text.lower(), "the cleanup script must name what it spares"
        assert "pythonw" not in text.split("Stop-Process")[0][-200:], (
            "no blanket pythonw filter next to a Stop-Process"
        )
