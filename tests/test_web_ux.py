"""UX of the Connexions panel and the development loop.

These tests read the interface files the way the browser does - they are the only
artifact a unit test can hold - and the factory the reloader uses. The live proof
(clicking the button in a real window) is a CDP run, not a test.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "uh" / "web" / "static" / "app.js").read_text(encoding="utf-8")


def test_each_remote_server_has_its_own_test_button() -> None:
    start = APP_JS.index("function httpServerRow(")
    body = APP_JS[start : start + 3000]
    assert '"Tester la connexion"' in body
    assert "api(`/api/mcp/${encodeURIComponent(server.name)}/test`" in body
    assert "duration_ms" in body, "the answer must show how long it took"


def test_a_save_tests_in_the_same_gesture() -> None:
    start = APP_JS.index("async function loadConnections(")
    form = APP_JS[start - 8000 : start]
    assert '"Enregistrer et tester"' in form
    assert "result.connection" in form
    assert "loadConnections()" in form, "the list is redrawn from the server's answer"


def test_the_states_are_spelled_for_a_person() -> None:
    for state, label in (
        ("auth", "jeton refusé"),
        ("unreachable", "injoignable"),
        ("incompatible", "protocole incompatible"),
        ("connected", "connecté"),
        ("configured", "configuré"),
    ):
        assert f'{state}: "{label}"' in APP_JS
    # A code alone is not an explanation: the advice rides with the state.
    assert "result.advice" in APP_JS


def test_a_remote_call_is_asked_before_it_runs() -> None:
    start = APP_JS.index("function approvalCard(")
    body = APP_JS[start : start + 2500]
    assert '"Autoriser"' in body and '"Refuser"' in body
    assert "/api/mcp/approvals/" in body
    assert 'event === "approval"' in APP_JS


def test_the_first_launch_offers_the_link_not_a_command() -> None:
    start = APP_JS.index("function firstRunHint(")
    body = APP_JS[start : start + 1600]
    assert '"/?panel=conn"' in body
    assert '"/?panel=local"' in body
    assert first_call_present(), "the hint must actually be shown"


def test_the_first_launch_is_a_banner_not_a_wall() -> None:
    """Opening the cockpit must never wait for a provider choice."""
    assert "maybeOpenSetup" not in APP_JS, "the blocking setup modal is gone"
    start = APP_JS.index("async function showSetupBanner(")
    body = APP_JS[start : start + 2500]
    assert 'banner.hidden = false' in body
    assert "modal-backdrop" not in body, "a banner is not a modal"
    assert "localStorage.setItem(SETUP_DISMISSED" in body, "« Plus tard » is remembered"
    boot = APP_JS[APP_JS.index("async function boot(") :][:6000]
    assert "showSetupBanner()" in boot
    html = (ROOT / "uh" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="setup-banner"' in html


def test_the_tabs_separate_keys_local_ai_and_connections() -> None:
    html = (ROOT / "uh" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    # The three first tabs are the ones a person sees without scrolling.
    keys = html.index('id="tab-keys"')
    local = html.index('id="tab-local"')
    conn = html.index('id="tab-conn"')
    assert keys < local < conn, "Clé API, IA locale, Connexions, in that order"
    assert "Clé API</button>" in html
    assert "IA locale</button>" in html
    assert 'id="local-view"' in html and 'id="local-panel"' in html
    assert '"local"' in APP_JS.split("const PANEL_TABS = new Set([")[1].split("])")[0]
    assert 'showPanelTab("local")' in APP_JS
    assert 'loadLocalPanel().catch' in APP_JS


def test_the_local_ai_panel_installs_nothing_behind_your_back() -> None:
    constants = APP_JS[APP_JS.index("const OLLAMA_DOWNLOAD") :][:400]
    assert "https://ollama.com/download" in constants
    assert "huggingface.co/docs/text-generation-inference" in constants
    helper = APP_JS[APP_JS.index("function localLink(") :][:600]
    assert "noopener noreferrer" in helper, "an external link opens in a new tab, safely"
    start = APP_JS.index("async function loadLocalPanel(")
    end = APP_JS.index("// cloud connections")
    body = APP_JS[start:end]
    assert "OLLAMA_DOWNLOAD" in body, "the guide links to the official installer"
    assert "UH n'installe pas Ollama à ta place" in body
    assert "127.0.0.1:8080" in body, "TGI local is detected, not installed"
    assert "/api/models/pull" in body
    assert 'showPanelTab("keys")' in body



def first_call_present() -> bool:
    return "if (!state.sessionId && !newest) firstRunHint();" in APP_JS


def test_the_stale_server_message_says_what_to_do() -> None:
    assert "relance `uh web`" in APP_JS
    # And the dev loop exists, so that message is rare while working on the UI.
    dev = (ROOT / "uh" / "web" / "dev.py").read_text(encoding="utf-8")
    assert "def app()" in dev and "UH_WEB_TOKEN" in dev


def test_the_development_factory_reads_the_environment(monkeypatch) -> None:
    import pytest

    pytest.importorskip("fastapi")
    from uh.web import dev

    monkeypatch.setenv("UH_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("UH_WEB_PORT", "8799")
    monkeypatch.setenv("UH_WEB_TOKEN", "dev-token")
    monkeypatch.setenv("UH_WEB_ALLOW_TERMINAL", "1")
    monkeypatch.setenv("UH_WEB_WORKSPACE", str(ROOT))
    settings = dev.settings()
    assert settings["port"] == 8799
    assert settings["token"] == "dev-token"
    assert settings["allow_terminal"] is True
    assert str(settings["workspace"]) == str(ROOT)
