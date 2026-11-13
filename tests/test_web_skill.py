"""A skill attached to a turn: the block reaches the provider, and only then.

The point of the test is the shape of the payload the model receives - the system
message carries the instructions, the user message stays what the user typed, and
an unknown skill is a refusal the browser can show.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import skills  # noqa: E402
from uh.console import system_message  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "skill-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class CapturingProvider:
    """Answers immediately and keeps the messages it was given."""

    model = "stub:1b"

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools, *, on_token=None):
        self.seen.append([dict(message) for message in messages])
        if on_token is not None:
            on_token("ok")
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    provider = CapturingProvider()
    app = create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: provider,
        kind="stub",
        model="stub:1b",
    )
    return app, provider


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# the block itself
# --------------------------------------------------------------------------- #
def test_the_block_is_labelled_and_carries_the_instructions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    skills.create(
        title="Relire un diff",
        description="avant de committer",
        instructions="Lis le diff.\nCherche les secrets.",
        home=tmp_path,
    )

    block = skills.prompt_block("relire-un-diff", home=tmp_path)

    assert block is not None
    assert block.startswith("## Active skill: Relire un diff (relire-un-diff)")
    assert "Cherche les secrets." in block


def test_an_unknown_skill_has_no_block(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path))

    assert skills.prompt_block("absent", home=tmp_path) is None
    assert skills.prompt_block("../config", home=tmp_path) is None


def test_the_system_message_appends_and_never_replaces(tmp_path: Path) -> None:
    plain = system_message(tmp_path)["content"]
    with_skill = system_message(tmp_path, "## Active skill: X")["content"]

    assert with_skill.startswith(plain)
    assert with_skill.endswith("## Active skill: X")
    assert system_message(tmp_path, "   ")["content"] == plain


# --------------------------------------------------------------------------- #
# through the chat route
# --------------------------------------------------------------------------- #
def test_a_turn_with_a_skill_sends_it_in_the_system_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = (
                await http.post(
                    "/api/skills",
                    json={
                        "title": "Verifier un deploiement",
                        "instructions": "Verifie la version, puis le health check.",
                    },
                    headers=AUTH,
                )
            ).json()
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            response = await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={"message": "lance la verification", "skill": created["name"]},
                headers=AUTH,
            )
            return response

    response = run(scenario())
    assert response.status_code == 200
    assert provider.seen, "the provider was called"
    messages = provider.seen[0]
    assert messages[0]["role"] == "system"
    assert "Active skill: Verifier un deploiement" in messages[0]["content"]
    assert "health check" in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "lance la verification"}


def test_a_turn_without_a_skill_carries_no_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            return await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={"message": "bonjour"},
                headers=AUTH,
            )

    run(scenario())
    assert "Active skill" not in provider.seen[0][0]["content"]


def test_an_unknown_skill_is_refused_before_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, provider = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            session = (await http.post("/api/sessions", json={}, headers=AUTH)).json()["session"]
            return await http.post(
                f"/api/sessions/{session['id']}/chat",
                json={"message": "bonjour", "skill": "n-existe-pas"},
                headers=AUTH,
            )

    response = run(scenario())
    assert response.status_code == 400
    assert "unknown skill" in response.json()["detail"]
    assert provider.seen == [], "a refused turn must not reach the provider"
