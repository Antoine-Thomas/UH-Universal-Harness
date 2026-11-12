"""Provider panel API (Jalon 7a): key values never cross the HTTP boundary.

Every test that touches a response goes through ``assert_no_key`` by bytes, not
by eye: the browser gets JSON, and the only acceptable mentions of a key are
"how many".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import keys as keys_mod  # noqa: E402
from uh.web.app import create_app  # noqa: E402
from uh.web.providers_api import is_loopback_bind, validate_base_url  # noqa: E402

TOKEN = "test-token-providers"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KNOWN_KEY = "known-key-value-must-never-leak-42"


def run(coro):
    return asyncio.run(coro)


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, host: str = "127.0.0.1"):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True, exist_ok=True)
    return create_app(
        token=TOKEN,
        port=8765,
        host=host,
        workspace=tmp_path,
        provider_factory=lambda kind, model: _StubProvider(),
        kind="stub",
        model="stub:1b",
    )


class _StubProvider:
    model = "stub:1b"
    streaming = True

    def chat(self, messages, tools, *, on_token=None):
        return {"role": "assistant", "content": "ok", "tool_calls": []}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def assert_no_key(payload: str) -> None:
    assert KNOWN_KEY not in payload, "a key value reached the HTTP response"
    # and no truncation of it either
    for length in (6, 8, 12):
        assert KNOWN_KEY[:length] not in payload


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:1234/v1",
        "http://localhost:20128/v1",
        "https://api.example.com/v1",
    ],
)
def test_valid_base_urls(url: str) -> None:
    assert validate_base_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    ("url", "fragment"),
    [
        ("http://api.example.com/v1", "https"),
        ("ftp://api.example.com/v1", "http or https"),
        ("https://user:pass@api.example.com/v1", "credentials"),
        ("https://api.example.com/v1?key=1", "query"),
        ("http://169.254.169.254/latest", "link-local"),
        ("https://169.254.169.254/latest", "link-local"),
        ("", "required"),
    ],
)
def test_refused_base_urls(url: str, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        validate_base_url(url)


def test_loopback_detection() -> None:
    assert is_loopback_bind("127.0.0.1")
    assert is_loopback_bind("localhost")
    assert is_loopback_bind("::1")
    assert is_loopback_bind("127.0.0.53")
    assert not is_loopback_bind("0.0.0.0")
    assert not is_loopback_bind("192.168.1.10")


# --------------------------------------------------------------------------- #
# GET /api/providers
# --------------------------------------------------------------------------- #
def test_providers_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.get("/api/providers")).status_code == 401

    run(scenario())


def test_providers_lists_the_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get("/api/providers", headers=AUTH)
            assert response.status_code == 200
            payload = response.json()
            names = {row["name"] for row in payload["providers"]}
            assert {"groq", "openrouter", "cerebras"} <= names
            row = next(item for item in payload["providers"] if item["name"] == "groq")
            assert row["verified"] == "verified"
            assert row["keys"] == 0
            assert row["in_chain"] is False
            assert row["custom"] is False
            assert set(row) == {
                "name", "base_url", "model", "keys", "verified", "custom", "in_chain",
            }

    run(scenario())


def test_known_key_never_appears_in_any_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "gpt-oss-120b", KNOWN_KEY)

    async def scenario():
        async with client(app) as http:
            bodies = []
            for path in ("/api/providers", "/api/status", "/api/models"):
                response = await http.get(path, headers=AUTH)
                bodies.append(response.text)
                assert_no_key(response.text)

            providers = json.loads(bodies[0])["providers"]
            groq = next(row for row in providers if row["name"] == "groq")
            assert groq["keys"] == 1  # the count is public, the value is not

    run(scenario())
