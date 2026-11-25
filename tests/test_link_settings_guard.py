"""La route qui change un réglage exige la même preuve que les autres écritures.

`POST /api/link/settings` active une fonction qui fait sortir des requêtes vers des
sites tiers. Un réglage qui change d'après un clic doit donc être aussi difficile à
déclencher qu'un envoi de message : un jeton, et une origine qui n'est pas étrangère.
Sans ça, une page ouverte dans le même navigateur pourrait l'appeler à la place de
l'opérateur.

Trois mesures, et le réglage relu après chacune.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from uh import link_preview
from uh.web.app import LINK_SETTINGS_PATH, create_app

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        workspace=tmp_path,
        provider_factory=lambda kind, model: object(),
        kind="stub",
        model="stub:1b",
    )


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


async def _test_without_a_token_the_setting_does_not_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    link_preview.save_enabled(False)

    async with client(app) as http:
        answer = await http.post(
            LINK_SETTINGS_PATH, json={"enabled": True}, headers={"Origin": BASE}
        )

    assert answer.status_code in (401, 403), answer.text
    assert link_preview.load_enabled() is False, "un réglage n'a pas bougé"


async def _test_a_foreign_origin_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une autre page du navigateur ne peut pas activer les aperçus à sa place."""
    app = make_app(tmp_path, monkeypatch)
    link_preview.save_enabled(False)

    async with client(app) as http:
        answer = await http.post(
            LINK_SETTINGS_PATH,
            json={"enabled": True},
            headers={**AUTH, "Origin": "https://ailleurs.example"},
        )

    assert answer.status_code in (400, 403), answer.text
    assert link_preview.load_enabled() is False


async def _test_the_operator_origin_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    link_preview.save_enabled(False)

    async with client(app) as http:
        answer = await http.post(
            LINK_SETTINGS_PATH, json={"enabled": True}, headers={**AUTH, "Origin": BASE}
        )

    assert answer.status_code == 200, answer.text
    assert answer.json()["enabled"] is True
    assert link_preview.load_enabled() is True


# Ce dépôt ne dépend pas de pytest-asyncio : les tests asynchrones s'exécutent par
# , comme le reste du fichier de routes.
def test_without_a_token_the_setting_does_not_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(_test_without_a_token_the_setting_does_not_move(tmp_path, monkeypatch))


def test_a_foreign_origin_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_a_foreign_origin_is_refused(tmp_path, monkeypatch))


def test_the_operator_origin_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asyncio.run(_test_the_operator_origin_is_accepted(tmp_path, monkeypatch))
