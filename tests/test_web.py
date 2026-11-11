"""Web interface: ASGI tests, no real model, no network.

The app is exercised with ``httpx.ASGITransport``; each test drives the async
client with ``asyncio.run`` so no async pytest plugin is required.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import keys as keys_mod  # noqa: E402
from uh.store import Store, display_id  # noqa: E402
from uh.web.app import create_app  # noqa: E402
from uh.web.sse import parse_frames  # noqa: E402

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"


class ScriptedProvider:
    """Streams two tokens, optionally emits one tool call first."""

    model = "stub:1b"

    def __init__(self, tool: Optional[str] = None) -> None:
        self.tool = tool
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.tool and self.calls == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": self.tool, "arguments": {"command": "echo hi"}}}],
            }
        for token in ("bon", "jour"):
            if on_token is not None:
                on_token(token)
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider=None, **kwargs):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    provider = provider or ScriptedProvider()
    return create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: provider,
        kind="stub",
        model="stub:1b",
        **kwargs,
    )


def run(coro):
    return asyncio.run(coro)


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


AUTH = {"Authorization": f"Bearer {TOKEN}"}


# --------------------------------------------------------------------------- #
# authentication and browser guards
# --------------------------------------------------------------------------- #
def test_api_requires_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            for path in ("/api/status", "/api/sessions", "/api/models", "/api/search?q=x"):
                response = await http.get(path)
                assert response.status_code == 401, path
            chat = await http.post("/api/sessions/abc/chat", json={"message": "hi"})
            assert chat.status_code == 401

    run(scenario())


def test_bad_token_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get("/api/status", headers={"Authorization": "Bearer wrong"})
            assert response.status_code == 401

    run(scenario())


def test_index_sets_the_cookie_and_survives_a_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            denied = await http.get("/")
            assert denied.status_code == 401

            page = await http.get(f"/?token={TOKEN}")
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
            assert "uh_token" in page.cookies or "uh_token" in str(page.headers.get("set-cookie"))

            again = await http.get("/")  # cookie only
            assert again.status_code == 200

    run(scenario())


def test_host_and_origin_are_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            bad_host = await http.get("/api/status", headers={**AUTH, "Host": "evil.example:8765"})
            assert bad_host.status_code == 400

            bad_origin = await http.get(
                "/api/status", headers={**AUTH, "Origin": "http://evil.example:8765"}
            )
            assert bad_origin.status_code == 400

            good = await http.get("/api/status", headers={**AUTH, "Origin": BASE})
            assert good.status_code == 200

    run(scenario())


def test_security_headers_and_no_inline_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get(f"/?token={TOKEN}")
            csp = response.headers["content-security-policy"]
            assert "default-src 'none'" in csp
            assert "script-src 'self'" in csp
            assert "unsafe-inline" not in csp
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["x-content-type-options"] == "nosniff"

            html = response.text
            assert "<script type=\"module\" src=\"/static/app.js\">" in html
            assert html.count("<script") == 1  # no inline block
            assert "onclick=" not in html and "style=" not in html

    run(scenario())


def test_static_assets_are_served_with_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            js = await http.get("/static/app.js")
            assert js.status_code == 200
            assert "javascript" in js.headers["content-type"]
            assert "default-src 'none'" in js.headers["content-security-policy"]

            css = await http.get("/static/style.css")
            assert css.status_code == 200
            assert "text/css" in css.headers["content-type"]

            page = await http.get(f"/?token={TOKEN}")
            assert "/static/app.js" in page.text and "/static/style.css" in page.text

    run(scenario())


def test_static_files_are_in_the_package() -> None:
    from uh.web import app as web_app

    for name in ("index.html", "app.js", "style.css"):
        assert (web_app.STATIC_DIR / name).is_file(), name


# --------------------------------------------------------------------------- #
# sessions CRUD, messages, search
# --------------------------------------------------------------------------- #
def test_sessions_crud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (await http.post("/api/sessions", json={"title": "hello"}, headers=AUTH)).json()
            session_id = created["session"]["id"]
            assert created["session"]["short_id"] == display_id(session_id)

            listing = (await http.get("/api/sessions", headers=AUTH)).json()
            assert [s["id"] for s in listing["sessions"]] == [session_id]

            renamed = (
                await http.patch(f"/api/sessions/{session_id}", json={"title": "renamed"}, headers=AUTH)
            ).json()
            assert renamed["title"] == "renamed"

            messages = (await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)).json()
            assert messages["messages"] == []
            assert messages["model"] == "stub:1b"

            deleted = (await http.delete(f"/api/sessions/{session_id}", headers=AUTH)).json()
            assert deleted["deleted"] is True
            assert (await http.get("/api/sessions", headers=AUTH)).json()["sessions"] == []

    run(scenario())


def test_unknown_session_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.get("/api/sessions/nope/messages", headers=AUTH)).status_code == 404
            assert (await http.post("/api/sessions/nope/stop", headers=AUTH)).status_code == 404

    run(scenario())


# --------------------------------------------------------------------------- #
# SSE chat
# --------------------------------------------------------------------------- #
def test_chat_streams_tokens_and_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (
                await http.post("/api/sessions", json={"title": "t"}, headers=AUTH)
            ).json()["session"]["id"]

            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "bonjour"}, headers=AUTH
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            frames = parse_frames(response.text)
            names = [name for name, _ in frames]
            assert names[0] == "open"
            assert names.count("token") == 2
            assert "answer" in names
            assert names[-1] == "done"
            assert "".join(data.get("text", "") for name, data in frames if name == "token") == "bonjour"

            messages = (await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)).json()
            roles = [m["role"] for m in messages["messages"]]
            assert roles == ["user", "assistant"]

    run(scenario())


def test_tool_events_and_terminal_disabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(tool="terminal")
    app = make_app(tmp_path, monkeypatch, provider=provider)

    async def scenario():
        async with client(app) as http:
            status = (await http.get("/api/status", headers=AUTH)).json()
            assert status["terminal"] is False

            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "ls"}, headers=AUTH
            )
            frames = dict(parse_frames(response.text))
            assert "tool_call" in frames
            assert frames["tool_call"]["name"] == "terminal"
            result = frames["tool_result"]
            assert result["name"] == "terminal"
            assert "unknown tool" in result["content"]

    run(scenario())


def test_terminal_can_be_enabled_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ScriptedProvider(tool="terminal")
    app = make_app(tmp_path, monkeypatch, provider=provider, allow_terminal=True)

    async def scenario():
        async with client(app) as http:
            status = (await http.get("/api/status", headers=AUTH)).json()
            assert status["terminal"] is True
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "echo hi"},
                headers=AUTH,
            )
            frames = dict(parse_frames(response.text))
            assert "unknown tool" not in frames["tool_result"]["content"]

    run(scenario())


def test_stop_without_a_running_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            payload = (await http.post(f"/api/sessions/{session_id}/stop", headers=AUTH)).json()
            assert payload["stopped"] is False

    run(scenario())


def test_search_returns_snippets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={"title": "find me"}, headers=AUTH)).json()[
                "session"
            ]["id"]
            await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "le clavier est bleu"}, headers=AUTH
            )
            hits = (await http.get("/api/search?q=clavier", headers=AUTH)).json()["hits"]
            assert hits and "clavier" in hits[0]["snippet"]
            assert hits[0]["short_id"] == display_id(session_id)

    run(scenario())


# --------------------------------------------------------------------------- #
# models, commands, secrets
# --------------------------------------------------------------------------- #
def test_models_and_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            models = (await http.get("/api/models", headers=AUTH)).json()
            assert models["model"] == "stub:1b"
            assert models["provider"] == "stub"  # the provider itself is injected
            assert {"ollama", "omnirouter"} <= set(models["providers"])

            commands = (await http.get("/api/commands", headers=AUTH)).json()["commands"]
            names = {command["name"] for command in commands}
            assert {"help", "model", "sessions", "exit"} <= names

            switched = (
                await http.post("/api/model", json={"model": "autre:7b"}, headers=AUTH)
            ).json()
            assert switched["model"] == "autre:7b"
            assert (await http.get("/api/status", headers=AUTH)).json()["model"] == "autre:7b"

    run(scenario())


def test_no_provider_key_is_ever_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    secret = "test-key-not-a-real-secret"
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "llama", secret)

    async def scenario():
        async with client(app) as http:
            for path in ("/api/status", "/api/models", "/api/commands"):
                body = (await http.get(path, headers=AUTH)).text
                assert secret not in body, path

    run(scenario())


def test_chat_without_message_is_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": "  "}, headers=AUTH
            )
            assert response.status_code == 400

    run(scenario())


def test_session_model_override_is_per_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            first = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]
            second = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]["id"]

            await http.post(
                "/api/model",
                json={"model": "perso:3b", "session_id": first},
                headers=AUTH,
            )
            a = (await http.get(f"/api/sessions/{first}/messages", headers=AUTH)).json()
            b = (await http.get(f"/api/sessions/{second}/messages", headers=AUTH)).json()
            assert a["model"] == "perso:3b"
            assert b["model"] == "stub:1b"

    run(scenario())
