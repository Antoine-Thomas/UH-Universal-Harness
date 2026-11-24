"""La route /api/media/thumb : jeton, garde d'origine, débit, cache, refus.

Le réseau est simulé : `media_proxy.fetch` est remplacé, donc aucun test ne sort
de la machine. Ce qui est vérifié ici, c'est le contrat de la route — pas les
règles du proxy, testées dans `tests/test_media_proxy.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import media_proxy  # noqa: E402
from uh.web.app import THUMB_PATH, create_app  # noqa: E402

TOKEN = "test-token-0123456789"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48
THUMB = "https://api.openverse.org/v1/images/x/thumb/"


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch):
    """Remplace le proxy par une fonction qui compte ses appels."""
    calls: list[str] = []

    def fetch(url: str, **_kwargs: object) -> media_proxy.Thumb:
        calls.append(url)
        return media_proxy.Thumb(mime="image/png", data=PNG)

    monkeypatch.setattr(media_proxy, "fetch", fetch)
    return calls


def test_the_route_requires_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario() -> None:
        async with client(app) as http:
            response = await http.get(THUMB_PATH, params={"url": THUMB})
        assert response.status_code == 401

    run(scenario())


def test_an_allowed_thumbnail_comes_back_as_bytes_with_its_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_fetch: list[str]
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.get(THUMB_PATH, params={"url": THUMB}, headers=AUTH)

    response = run(scenario())

    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["cache-control"].startswith("private, max-age=")
    assert fake_fetch == [THUMB]


def test_the_second_call_is_served_from_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_fetch: list[str]
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            first = await http.get(THUMB_PATH, params={"url": THUMB}, headers=AUTH)
            second = await http.get(THUMB_PATH, params={"url": THUMB}, headers=AUTH)
        return first, second

    first, second = run(scenario())

    assert first.content == second.content == PNG
    assert len(fake_fetch) == 1, "la même vignette n'est demandée qu'une fois au site"


def test_a_refused_url_is_a_400_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    def refuse(_url: str, **_kwargs: object) -> media_proxy.Thumb:
        raise media_proxy.MediaRefused("hôte hors liste : exemple.fr")

    monkeypatch.setattr(media_proxy, "fetch", refuse)

    async def scenario():
        async with client(app) as http:
            return await http.get(
                THUMB_PATH, params={"url": "https://exemple.fr/x.png"}, headers=AUTH
            )

    response = run(scenario())
    assert response.status_code == 400
    assert "hôte hors liste" in response.json()["detail"]


def test_the_thirty_first_request_of_the_minute_is_a_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_fetch: list[str]
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        statuses = []
        async with client(app) as http:
            for _ in range(31):
                reply = await http.get(
                    THUMB_PATH, params={"url": THUMB, "session": "s1"}, headers=AUTH
                )
                statuses.append(reply.status_code)
            return statuses, reply

    statuses, last = run(scenario())

    assert statuses[:30] == [200] * 30
    assert statuses[30] == 429
    assert "par minute" in last.json()["detail"]


def test_each_session_has_its_own_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_fetch: list[str]
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            for _ in range(30):
                await http.get(THUMB_PATH, params={"url": THUMB, "session": "s1"}, headers=AUTH)
            blocked = await http.get(
                THUMB_PATH, params={"url": THUMB, "session": "s1"}, headers=AUTH
            )
            other = await http.get(THUMB_PATH, params={"url": THUMB, "session": "s2"}, headers=AUTH)
        return blocked, other

    blocked, other = run(scenario())
    assert blocked.status_code == 429
    assert other.status_code == 200


def test_the_origin_guard_still_applies_to_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_fetch: list[str]
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.get(
                THUMB_PATH,
                params={"url": THUMB},
                headers={**AUTH, "Origin": "https://evil.example"},
            )

    response = run(scenario())
    assert response.status_code == 403
    assert "origin not allowed" in response.json()["detail"]
    assert fake_fetch == []


def test_a_missing_url_is_refused_by_the_framework(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_fetch: list[str]
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            return await http.get(THUMB_PATH, headers=AUTH)

    response = run(scenario())
    assert response.status_code == 422
    assert fake_fetch == []
