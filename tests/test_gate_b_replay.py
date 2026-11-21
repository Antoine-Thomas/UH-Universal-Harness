"""The two lines gate B replayed: the token case, and a re-kept image.

Both were bench defects first, and one of them hid a real hole: a second « Garder »
on the same picture returned an entry without its dimensions, so the memory would
show "None × None" for something it had perfectly well kept.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("PIL")

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from uh import memory  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-gate-b"
BASE = "http://127.0.0.1:8765"


class StubProvider:
    model = "stub:1b"

    def chat(self, messages, tools=None, stream=False, **kwargs):  # pragma: no cover
        raise RuntimeError("no turn runs here")


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        host="127.0.0.1",
        workspace=tmp_path,
        provider_factory=lambda kind, model: StubProvider(),
        kind="stub",
        model="stub:1b",
    )


def post_mode(app, headers: dict) -> int:
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            response = await http.post("/api/uhb/mode", headers=headers, json={"mode": "dev"})
            return response.status_code

    return asyncio.run(run())


def test_a_missing_token_with_a_valid_origin_is_a_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """403 is the middleware (cross-origin, or a cookie with no origin proof); 401 is
    the route's token gate. Both are refusals, and the bench line says which one."""
    app = make_app(tmp_path, monkeypatch)
    assert post_mode(app, {"Origin": BASE}) == 401
    assert post_mode(app, {"Origin": BASE, "Authorization": "Bearer "}) == 401
    assert post_mode(app, {}) == 401, "no cookie, no origin: nothing to forge, the token decides"


def test_a_cookie_without_an_origin_proof_is_refused_by_the_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            response = await http.post(
                "/api/uhb/mode",
                json={"mode": "dev"},
                headers={"Authorization": ""},
                cookies={"uh_token_8765": TOKEN},
            )
            return response.status_code, response.text

    status, body = asyncio.run(run())
    assert status == 403 and "preuve d'origine" in body


def test_a_cross_origin_request_is_refused_even_with_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    assert post_mode(app, {"Origin": "https://evil.example"}) == 403


def test_keeping_the_same_picture_twice_still_describes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    picture = tmp_path / "shot.png"
    Image.effect_noise((640, 360), 80).convert("RGB").save(picture)

    first = memory.keep(picture, kind="image")
    second = memory.keep(picture, kind="image")

    assert second["sha256"] == first["sha256"]
    assert (second.get("width"), second.get("height")) == (640, 360), (
        "a re-keep must not answer None × None about a picture it holds"
    )
    assert second["bytes"] == first["bytes"]
    assert len(memory.entries()) == 1
    json.dumps(second)
