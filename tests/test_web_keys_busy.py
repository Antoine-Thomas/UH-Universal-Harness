"""La route qui supprime une cle traduit l'echec d'ecriture : 503 lisible, pas 500.

Ce test existe parce que la porte de push a attrape un `F821` : la clause
`except keys.KeysBusy` nommait un module absent. Le chemin normal passait, mais le
jour ou l'ecriture echoue vraiment, Python evaluait la clause et levait `NameError`
— donc un 500 brut, exactement ce que le correctif devait supprimer. Aucun test ne
couvrait ce chemin ; celui-ci le couvre, au niveau de la route et pas de l'exception.

La cle canari ne doit apparaitre ni dans le corps de la reponse ni dans un chemin.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import keys as keys_mod  # noqa: E402
from uh.web import providers_api  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-keys-busy"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CANARY = "canari-ne-doit-jamais-sortir-1234"


class _StubProvider:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


@pytest.fixture()
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: _StubProvider(),
        kind="stub",
        model="stub:1b",
    )


def test_a_busy_key_file_answers_503_with_a_readable_message(app, monkeypatch) -> None:
    keys_mod.add_provider("deepseek", "https://example.com", "fake-1b", CANARY)

    def busy(name: str, index: int) -> int:
        raise keys_mod.KeysBusy("fichier de clés occupé, réessayez")

    monkeypatch.setattr(providers_api, "remove_key_index", busy)

    async def call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.request(
                "DELETE",
                "/api/providers/deepseek/keys",
                json={"index": 0},
                headers=AUTH,
            )

    response = asyncio.run(call())

    assert response.status_code == 503, response.text
    body = response.text
    assert "fichier de clés occupé, réessayez" in body, body
    assert CANARY not in body, "la cle doit rester hors de la reponse"
    assert "keys.toml" not in body, "aucun chemin de fichier dans le corps"
