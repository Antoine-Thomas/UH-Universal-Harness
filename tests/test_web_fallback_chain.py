"""`uh web` walks the same fallback chain as `uh chat`.

Ollama -> Cloudflare -> DeepSeek -> OmniRouter -> Router: a network failure or a
quota moves on to the next provider and says so in the transcript; a refused
credential (401/402/403) stops, because answering from another account would hide
a bad key. Providers are scripted, nothing leaves the machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_web import AUTH, client, make_app, run
from uh.providers import ProviderError
from uh.web.sse import parse_frames


class Link:
    def __init__(self, answer: str | None = None, error: ProviderError | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls = 0
        self.model = "stub:1b"

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"role": "assistant", "content": self.answer, "tool_calls": []}


def turn(app, text: str = "salut"):
    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()[
                "session"
            ]["id"]
            response = await http.post(
                f"/api/sessions/{session_id}/chat", json={"message": text}, headers=AUTH
            )
            messages = (
                await http.get(f"/api/sessions/{session_id}/messages", headers=AUTH)
            ).json()["messages"]
            return parse_frames(response.text), messages

    return run(scenario())


def test_a_quota_on_the_first_provider_falls_back_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Link(error=ProviderError("quota", status=429))
    second = Link(answer="réponse du second")
    app = make_app(tmp_path, monkeypatch, provider=first)
    app.state.fallback_chain = [("stub", first), ("deepseek", second)]

    frames, messages = turn(app)

    assert (first.calls, second.calls) == (1, 1)
    assert messages[-1]["content"] == "réponse du second"
    notices = [data for name, data in frames if name == "notice"]
    assert any("Bascule" in str(item) for item in notices), frames


def test_a_refused_credential_does_not_fall_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Link(error=ProviderError("bad key", status=401))
    second = Link(answer="jamais")
    app = make_app(tmp_path, monkeypatch, provider=first)
    app.state.fallback_chain = [("stub", first), ("deepseek", second)]

    turn(app)

    assert second.calls == 0, "a 401 is reported, never answered by another account"


def test_a_model_chosen_for_the_conversation_stays_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Link(error=ProviderError("quota", status=429))
    second = Link(answer="jamais")
    app = make_app(tmp_path, monkeypatch, provider=first)
    app.state.fallback_chain = [("stub", first), ("deepseek", second)]

    async def scenario():
        async with client(app) as http:
            session_id = (await http.post("/api/sessions", json={}, headers=AUTH)).json()[
                "session"
            ]["id"]
            app.state.session_models[session_id] = ("stub", "stub:1b")
            await http.post(f"/api/sessions/{session_id}/chat", json={"message": "x"}, headers=AUTH)

    run(scenario())
    assert second.calls == 0


def test_without_a_chain_the_behaviour_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    only = Link(answer="seul")
    app = make_app(tmp_path, monkeypatch, provider=only)
    assert app.state.fallback_chain == []
    _frames, messages = turn(app)
    assert messages[-1]["content"] == "seul"
