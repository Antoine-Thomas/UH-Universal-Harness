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
from uh.providers_admin import AdminError, is_loopback_bind, validate_base_url  # noqa: E402
from uh.web.app import create_app  # noqa: E402

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
    with pytest.raises(AdminError, match=fragment):
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
            assert {"openrouter", "cerebras", "nvidia"} <= names
            row = next(item for item in payload["providers"] if item["name"] == "openrouter")
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

# --------------------------------------------------------------------------- #
# 7a.2 — key entry: write-only, loopback only, deletion by index
# --------------------------------------------------------------------------- #
def test_key_entry_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post("/api/providers/groq/keys", json={"key": KNOWN_KEY})
            assert response.status_code == 401

    run(scenario())


def test_key_entry_refused_off_loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch, host="0.0.0.0")

    async def scenario():
        async with client(app) as http:
            listing = (await http.get("/api/providers", headers=AUTH)).json()
            assert listing["key_entry_allowed"] is False

            response = await http.post(
                "/api/providers/groq/keys", json={"key": KNOWN_KEY}, headers=AUTH
            )
            assert response.status_code == 403
            assert_no_key(response.text)

            deletion = await http.request(
                "DELETE", "/api/providers/groq/keys", json={"index": 1}, headers=AUTH
            )
            assert deletion.status_code == 403

    run(scenario())
    # nothing was written
    assert keys_mod.get_provider("groq") is None


def test_add_key_stores_without_echoing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/openrouter/keys", json={"key": KNOWN_KEY}, headers=AUTH
            )
            assert response.status_code == 200
            assert response.json() == {"stored": True, "keys": 1}
            assert_no_key(response.text)

            listing = await http.get("/api/providers", headers=AUTH)
            assert_no_key(listing.text)
            row = next(row for row in listing.json()["providers"] if row["name"] == "openrouter")
            assert row["keys"] == 1

    run(scenario())
    entry = keys_mod.get_provider("openrouter")
    assert entry is not None and entry["keys"] == [KNOWN_KEY]
    assert oct((tmp_path / "home" / "keys.toml").stat().st_mode & 0o777) in {"0o600", "0o666"}


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({}, 400),
        ({"key": ""}, 400),
        ({"key": "   "}, 400),
        ({"key": "x" * 600}, 400),
        ({"key": "abc\ndef"}, 400),
    ],
)
def test_bad_key_payloads_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict, status: int
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post("/api/providers/groq/keys", json=payload, headers=AUTH)
            assert response.status_code == status

    run(scenario())


def test_unknown_provider_is_404_and_does_not_echo_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/nope/keys", json={"key": KNOWN_KEY}, headers=AUTH
            )
            assert response.status_code == 404
            assert_no_key(response.text)

    run(scenario())


def test_delete_key_by_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", "first-key")
    keys_mod.add_key("groq", "second-key")

    async def scenario():
        async with client(app) as http:
            removed = await http.request(
                "DELETE", "/api/providers/groq/keys", json={"index": 1}, headers=AUTH
            )
            assert removed.status_code == 200
            assert removed.json() == {"deleted": True, "keys": 1}

            missing = await http.request(
                "DELETE", "/api/providers/groq/keys", json={"index": 5}, headers=AUTH
            )
            assert missing.status_code == 404

            bad = await http.request(
                "DELETE", "/api/providers/groq/keys", json={"index": 0}, headers=AUTH
            )
            assert bad.status_code == 400

    run(scenario())
    assert keys_mod.get_provider("groq")["keys"] == ["second-key"]

# --------------------------------------------------------------------------- #
# 7a.3 — custom provider and connection test
# --------------------------------------------------------------------------- #
def test_custom_provider_refused_off_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch, host="0.0.0.0")

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/custom",
                json={"name": "perso", "base_url": "https://api.example.com/v1", "model": "m"},
                headers=AUTH,
            )
            assert response.status_code == 403

    run(scenario())


def test_custom_provider_created_and_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            created = await http.post(
                "/api/providers/custom",
                json={
                    "name": "perso",
                    "base_url": "https://api.example.com/v1",
                    "model": "some-model",
                    "key": KNOWN_KEY,
                },
                headers=AUTH,
            )
            assert created.status_code == 200
            assert created.json()["created"] is True
            assert_no_key(created.text)

            listing = await http.get("/api/providers", headers=AUTH)
            assert_no_key(listing.text)
            row = next(item for item in listing.json()["providers"] if item["name"] == "perso")
            assert row["custom"] is True
            assert row["verified"] == "custom"
            assert row["keys"] == 1

    run(scenario())
    assert keys_mod.get_provider("perso")["keys"] == [KNOWN_KEY]


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"name": "Bad Name", "base_url": "https://x.test/v1"}, "lowercase"),
        ({"name": "perso", "base_url": "http://api.example.com/v1"}, "https"),
        ({"name": "perso", "base_url": "http://169.254.169.254/latest"}, "link-local"),
        ({"name": "perso", "base_url": "ftp://x.test"}, "http or https"),
        ({"name": "openrouter", "base_url": "https://x.test/v1"}, "catalogue provider"),
        ({"name": "perso", "base_url": ""}, "required"),
    ],
)
def test_custom_provider_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict, fragment: str
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post("/api/providers/custom", json=payload, headers=AUTH)
            assert response.status_code == 400
            assert fragment in response.json()["detail"]

    run(scenario())


def _probe_app(tmp_path, monkeypatch, handler):
    app = make_app(tmp_path, monkeypatch)
    app.state.probe_transport = httpx.MockTransport(handler)
    return app


def test_probe_sends_a_ping_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "p"}}]})

    app = _probe_app(tmp_path, monkeypatch, handler)

    async def scenario():
        async with client(app) as http:
            result = await http.post("/api/providers/openrouter/test", headers=AUTH)
            assert result.status_code == 200
            assert result.json() == {"ok": True, "status": 200, "error_kind": None}

    run(scenario())
    assert seen[0]["messages"] == [{"role": "user", "content": "ping"}]
    assert seen[0]["max_tokens"] == 1


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (429, "rate_limit"), (500, "server"), (402, "payment")],
)
def test_probe_reports_the_error_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int, kind: str
) -> None:
    app = _probe_app(tmp_path, monkeypatch, lambda request: httpx.Response(status))

    async def scenario():
        async with client(app) as http:
            body = (await http.post("/api/providers/openrouter/test", headers=AUTH)).json()
            assert body["ok"] is False
            assert body["error_kind"] == kind
            assert body["status"] == status

    run(scenario())


def test_probe_refuses_a_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _probe_app(
        tmp_path,
        monkeypatch,
        lambda request: httpx.Response(302, headers={"location": "https://evil.test/steal"}),
    )

    async def scenario():
        async with client(app) as http:
            body = (await http.post("/api/providers/openrouter/test", headers=AUTH)).json()
            assert body["ok"] is False
            assert body["error_kind"] == "redirect_refused"

    run(scenario())


def test_probe_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow", request=request)

    app = _probe_app(tmp_path, monkeypatch, handler)

    async def scenario():
        async with client(app) as http:
            body = (await http.post("/api/providers/openrouter/test", headers=AUTH)).json()
            assert body == {"ok": False, "status": None, "error_kind": "timeout"}

    run(scenario())


def test_probe_unknown_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.post("/api/providers/nope/test", headers=AUTH)).status_code == 404

    run(scenario())

# --------------------------------------------------------------------------- #
# 7a.4 — allow_remote and consent
# --------------------------------------------------------------------------- #
def test_settings_require_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            assert (await http.get("/api/router/settings")).status_code == 401
            assert (await http.put("/api/router/settings", json={})).status_code == 401

    run(scenario())


def test_settings_default_to_local_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            body = (await http.get("/api/router/settings", headers=AUTH)).json()
            assert body["allow_remote"] is False
            assert body["consent"] is False
            assert "Free tiers" in body["warning"]

    run(scenario())


def test_enabling_remote_without_consent_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.put(
                "/api/router/settings", json={"allow_remote": True}, headers=AUTH
            )
            assert response.status_code == 400
            assert "consent" in response.json()["detail"]

            after = (await http.get("/api/router/settings", headers=AUTH)).json()
            assert after["allow_remote"] is False

    run(scenario())


def test_enabling_remote_with_consent_writes_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.put(
                "/api/router/settings",
                json={"allow_remote": True, "consent": True, "strategy": "round-robin"},
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["allow_remote"] is True
            assert body["consent"] is True
            assert body["consent_date"]
            assert body["strategy"] == "round-robin"

    run(scenario())

    from uh import config as uh_config

    cfg = uh_config.load()
    assert cfg.get("allow_remote") is True
    assert str(cfg.get("remote_consent") or "")
    assert cfg.get("router_strategy") == "round-robin"


def test_settings_refuse_off_loopback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, monkeypatch, host="0.0.0.0")

    async def scenario():
        async with client(app) as http:
            response = await http.put(
                "/api/router/settings",
                json={"allow_remote": True, "consent": True},
                headers=AUTH,
            )
            assert response.status_code == 403
            assert "privacy settings" in response.json()["detail"]

    run(scenario())


def test_settings_reject_unknown_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.put(
                "/api/router/settings", json={"strategy": "random"}, headers=AUTH
            )
            assert response.status_code == 400

    run(scenario())


# --------------------------------------------------------------------------- #
# popular provider presets
# --------------------------------------------------------------------------- #
def test_presets_carry_a_base_url_and_a_default_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.get("/api/providers/presets", headers=AUTH)
            assert response.status_code == 200
            assert_no_key(response.text)
            presets = {entry["name"]: entry for entry in response.json()["presets"]}
            assert {
                "openai",
                "mistral",
                "kimi",
                "gemini",
                "groq",
                "together",
            } <= set(presets)
            assert presets["openai"]["base_url"] == "https://api.openai.com/v1"
            assert presets["openai"]["model"] == "gpt-4o-mini"
            assert presets["mistral"]["base_url"] == "https://api.mistral.ai/v1"
            assert presets["kimi"]["model"] == "kimi-k2"
            assert presets["gemini"]["base_url"].endswith("/v1beta/openai/")
            assert presets["groq"]["model"] == "llama-3.3-70b-versatile"
            assert presets["together"]["model"].endswith("Instruct-Turbo")

    run(scenario())


def test_deepseek_is_a_builtin_preset_not_a_custom_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """UH ships DeepSeek: create_custom() refuses that name, so it has its own route."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            payload = (await http.get("/api/providers/presets", headers=AUTH)).json()
            assert_no_key(json.dumps(payload))

            builtin = {entry["name"]: entry for entry in payload["builtin"]}
            assert "deepseek" in builtin
            assert builtin["deepseek"]["base_url"] == "https://api.deepseek.com"
            assert builtin["deepseek"]["model"]
            assert builtin["deepseek"]["endpoint"] == "/api/providers/deepseek/key"
            # Not in the custom presets: a click there would end in a 400.
            assert "deepseek" not in {entry["name"] for entry in payload["presets"]}

    run(scenario())


def test_a_builtin_preset_saves_its_key_through_the_provider_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/deepseek/key", json={"key": KNOWN_KEY}, headers=AUTH
            )
            assert response.status_code == 200
            assert_no_key(response.text)
            assert response.json()["stored"] is True

    run(scenario())


def test_claude_is_gateway_only_and_never_a_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            payload = (await http.get("/api/providers/presets", headers=AUTH)).json()
            assert "anthropic" not in {entry["name"] for entry in payload["presets"]}
            note = next(entry for entry in payload["gateway_only"] if entry["name"] == "anthropic")
            assert "LiteLLM, OpenRouter, or OmniRoute" in note["note"]
            assert "not natively OpenAI-compatible" in note["note"]

    run(scenario())


def test_choosing_a_preset_saves_it_through_the_custom_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The form posts the preset's own fields: it must land as a custom provider."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            payload = (await http.get("/api/providers/presets", headers=AUTH)).json()
            preset = next(entry for entry in payload["presets"] if entry["name"] == "openai")

            created = await http.post(
                "/api/providers/custom",
                json={
                    "name": preset["name"],
                    "base_url": preset["base_url"],
                    "model": preset["model"],
                    "key": KNOWN_KEY,
                },
                headers=AUTH,
            )
            assert created.status_code == 200
            assert_no_key(created.text)

            listing = await http.get("/api/providers", headers=AUTH)
            assert_no_key(listing.text)
            row = next(
                item for item in listing.json()["providers"] if item["name"] == "openai"
            )
            assert row["base_url"] == preset["base_url"]
            assert row["model"] == preset["model"]
            assert row["custom"] is True and row["keys"] == 1

    run(scenario())


def test_presets_stay_read_only_while_the_panel_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preset is a form default, not a provider: nothing is stored by reading."""
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            await http.get("/api/providers/presets", headers=AUTH)
            listing = (await http.get("/api/providers", headers=AUTH)).json()
            assert "openai" not in {item["name"] for item in listing["providers"]}

    run(scenario())
