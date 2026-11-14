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
    body = APP_JS[start : start + 1200]
    assert '"/?panel=conn"' in body
    assert first_call_present(), "the hint must actually be shown"


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
