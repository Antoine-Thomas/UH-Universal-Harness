"""The settings route and the chat route: what the provider actually receives.

The provider is a stub that records the parameters and the tools it was given, so
"the slider works" means something checkable: the values arrive, the unsupported
ones do not, and the panel being off sends nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh.web.app import create_app  # noqa: E402

TOKEN = "settings-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class RecordingProvider:
    """Keeps the params and the tool names of every call."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.request_params: dict = {}
        self.calls: list[dict] = []

    def set_params(self, params):
        self.request_params = dict(params or {})

    def chat(self, messages, tools, *, on_token=None):
        self.calls.append(
            {
                "params": dict(self.request_params),
                "tools": [spec.get("function", {}).get("name") for spec in tools or []],
            }
        )
        if on_token is not None:
            on_token("ok")
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str = "stub"):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    provider = RecordingProvider()
    app = create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda k, m: provider,
        kind=kind,
        model="stub:1b",
    )
    return app, provider


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def run(coro):
    return asyncio.run(coro)


def test_the_route_needs_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/llm/settings")).status_code

    assert run(scenario()) == 401


def test_the_route_publishes_the_spec_and_the_support_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _provider = make_app(tmp_path, monkeypatch, kind="ollama")

    async def scenario():
        async with client(app) as http:
            return (await http.get("/api/llm/settings", headers=AUTH)).json()

    body = run(scenario())
    assert body["provider"] == "ollama"
    assert "temperature" in body["spec"]
    assert body["defaults"]["enabled"] is False
    assert body["support"]["provider"] == "ollama"
    assert "reasoning_effort" in [entry["name"] for entry in body["support"]["ignored"]]


def test_the_chat_route_forwards_the_settings_to_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            return await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={
                    "message": "bonjour",
                    "settings": {
                        "enabled": True,
                        "values": {"temperature": 0.2, "max_tokens": 512, "top_p": 0.9},
                    },
                },
                headers=AUTH,
            )

    response = run(scenario())
    assert response.status_code == 200
    assert provider.calls, "the provider answered a turn"
    params = provider.calls[0]["params"]
    assert params["temperature"] == 0.2
    assert params["max_tokens"] == 512
    assert params["top_p"] == 0.9


def test_the_panel_being_off_sends_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            return await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={
                    "message": "bonjour",
                    "settings": {"enabled": False, "values": {"temperature": 0.1}},
                },
                headers=AUTH,
            )

    run(scenario())
    assert provider.calls[0]["params"] == {}


def test_an_unknown_parameter_never_reaches_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            return await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={
                    "message": "bonjour",
                    "settings": {"enabled": True, "values": {"temperature": 0.3, "top_k": 40}},
                },
                headers=AUTH,
            )

    run(scenario())
    assert "top_k" not in provider.calls[0]["params"]
    assert provider.calls[0]["params"]["temperature"] == 0.3


def test_the_web_search_switch_removes_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={
                    "message": "cherche",
                    "settings": {"enabled": True, "values": {"web_search": False}},
                },
                headers=AUTH,
            )
            return session

    run(scenario())
    assert "web_search" not in provider.calls[0]["tools"]
    assert "read_file" in provider.calls[0]["tools"], "the other tools stay"


def test_settings_are_stored_on_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening a conversation must restore the settings it was written with."""
    app, _provider = make_app(tmp_path, monkeypatch)
    blob = {"enabled": True, "values": {"temperature": 0.35, "max_tokens": 256}}

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            patched = await http.patch(
                f"/api/sessions/{session['id']}", json={"settings": blob}, headers=AUTH
            )
            listed = (await http.get("/api/sessions", headers=AUTH)).json()["sessions"]
            return patched, listed

    patched, listed = run(scenario())
    assert patched.status_code == 200
    stored = listed[0]["metadata"]["settings"]
    assert stored["enabled"] is True
    assert stored["values"]["temperature"] == 0.35
    assert stored["values"]["max_tokens"] == 256


def test_a_patch_without_anything_to_change_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            return await http.patch(f"/api/sessions/{session['id']}", json={}, headers=AUTH)

    assert run(scenario()).status_code == 400
