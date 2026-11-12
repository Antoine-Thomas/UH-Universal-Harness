"""API-key panel: storage, masking and the Cloudflare/DeepSeek/OmniRoute flows.

Every response is checked by bytes: a full key must never cross the HTTP
boundary, and a masked hint must stay a hint (3 leading + 4 trailing
characters at most).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from uh import config as uh_config  # noqa: E402
from uh import keys as keys_mod  # noqa: E402
from uh.providers.cloudflare import CloudflareProvider  # noqa: E402
from uh.providers_admin import key_hint  # noqa: E402
from uh.web.app import create_app  # noqa: E402

TOKEN = "test-token-keys-panel"
BASE = "http://127.0.0.1:8765"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ACCOUNT = "0123456789abcdef0123456789abcdef"
CF_TOKEN = "cf-panel-token-must-never-leak-77"
DS_KEY = "ds-panel-key-must-never-leak-88"
OMNI_KEY = "omni-panel-key-must-never-leak-99"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No ambient credential, no real ~/.omniroute file: tests are hermetic."""
    for name in (
        "UH_CLOUDFLARE_ACCOUNT_ID",
        "UH_CLOUDFLARE_API_TOKEN",
        "UH_OMNIROUTER_URL",
        "OMNIROUTE_BASE_URL",
        "UH_OMNIROUTER_API_KEY",
        "OMNIROUTE_API_KEY",
        "UH_OMNIROUTER_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    # config_path() honours this override and returns None for a missing file.
    monkeypatch.setenv("UH_OMNIROUTER_CONFIG", str(tmp_path / "no-omniroute.env"))


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


def assert_no_full_key(payload: str, *keys: str) -> None:
    for key in keys:
        assert key not in payload, "a key value reached the HTTP response"
        for length in (6, 8, 12):
            assert key[:length] not in payload, "a key prefix reached the HTTP response"
        assert key[-8:] not in payload, "a key suffix reached the HTTP response"


# --------------------------------------------------------------------------- #
# the mask itself
# --------------------------------------------------------------------------- #
def test_key_hint_stays_a_hint() -> None:
    assert key_hint("short") == "••••"
    hint = key_hint(CF_TOKEN)
    assert hint.startswith("cf-") and hint.endswith(CF_TOKEN[-4:])
    assert len(hint) == 3 + 1 + 4  # 3 leading, an ellipsis, 4 trailing


def test_key_hint_hides_short_values_entirely() -> None:
    for value in ("", "abc", "sk-1234567", "x" * 11):
        assert key_hint(value) == "••••"


# --------------------------------------------------------------------------- #
# default state
# --------------------------------------------------------------------------- #
def test_keys_panel_lists_the_three_sections_in_priority_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            payload = (await http.get("/api/providers", headers=AUTH)).json()
            keys = payload["keys"]
            assert list(keys) == ["cloudflare", "deepseek", "omniroute"]
            assert keys["cloudflare"]["configured"] is False
            assert keys["cloudflare"]["source"] == "none"
            assert keys["deepseek"]["configured"] is False
            assert keys["omniroute"]["detected"] is False

    run(scenario())


# --------------------------------------------------------------------------- #
# Cloudflare: account id in config.toml, token in keys.toml
# --------------------------------------------------------------------------- #
def test_cloudflare_save_stores_both_files_and_returns_only_a_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/cloudflare/config",
                json={"account_id": ACCOUNT, "token": CF_TOKEN},
                headers=AUTH,
            )
            assert response.status_code == 200
            body = response.json()
            assert body["stored"] is True
            assert body["account_id"] == ACCOUNT
            assert body["key_hint"] == f"cf-…{CF_TOKEN[-4:]}"
            assert_no_full_key(response.text, CF_TOKEN)

            state = (await http.get("/api/providers", headers=AUTH)).json()["keys"]
            assert state["cloudflare"]["configured"] is True
            assert state["cloudflare"]["source"] == "keys.toml"
            assert state["cloudflare"]["key_hints"] == [f"cf-…{CF_TOKEN[-4:]}"]
            assert state["cloudflare"]["account_id"] == ACCOUNT

    run(scenario())
    stored = keys_mod.get_provider("cloudflare")
    assert stored is not None and stored["keys"] == [CF_TOKEN]
    assert stored["base_url"].endswith(f"/accounts/{ACCOUNT}/ai")
    assert uh_config.load().get("cloudflare_account_id") == ACCOUNT
    mode = oct((tmp_path / "home" / "keys.toml").stat().st_mode & 0o777)
    assert mode in {"0o600", "0o666"}


def test_cloudflare_refuses_a_wrong_account_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/cloudflare/config",
                json={"account_id": "not-hex", "token": CF_TOKEN},
                headers=AUTH,
            )
            assert response.status_code == 400
            assert "32 hexadecimal" in response.json()["detail"]
            assert_no_full_key(response.text, CF_TOKEN)

    run(scenario())
    assert keys_mod.get_provider("cloudflare") is None


def test_cloudflare_write_needs_a_loopback_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch, host="0.0.0.0")

    async def scenario():
        async with client(app) as http:
            response = await http.post(
                "/api/providers/cloudflare/config",
                json={"account_id": ACCOUNT, "token": CF_TOKEN},
                headers=AUTH,
            )
            assert response.status_code == 403

    run(scenario())
    assert keys_mod.get_provider("cloudflare") is None


def test_cloudflare_provider_uses_the_token_the_panel_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            await http.post(
                "/api/providers/cloudflare/config",
                json={"account_id": ACCOUNT, "token": CF_TOKEN},
                headers=AUTH,
            )

    run(scenario())
    provider = CloudflareProvider()  # env token deleted: keys.toml is the source
    assert provider.api_key == CF_TOKEN
    assert provider.base_url.endswith(f"/accounts/{ACCOUNT}/ai")


# --------------------------------------------------------------------------- #
# DeepSeek: append (rotation), hint only
# --------------------------------------------------------------------------- #
def test_deepseek_key_is_stored_then_rotated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = "ds-second-key-must-never-leak-55"
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            first = await http.post(
                "/api/providers/deepseek/key", json={"key": DS_KEY}, headers=AUTH
            )
            assert first.status_code == 200
            assert first.json()["keys"] == 1
            assert_no_full_key(first.text, DS_KEY)

            again = await http.post(
                "/api/providers/deepseek/key", json={"key": second}, headers=AUTH
            )
            assert again.status_code == 200
            assert again.json()["keys"] == 2
            assert again.json()["key_hints"] == [
                f"ds-…{DS_KEY[-4:]}",
                f"ds-…{second[-4:]}",
            ]
            assert_no_full_key(again.text, DS_KEY, second)

            page = await http.get("/api/providers", headers=AUTH)
            assert_no_full_key(page.text, DS_KEY, second)
            assert page.json()["keys"]["deepseek"]["configured"] is True

    run(scenario())
    stored = keys_mod.get_provider("deepseek")
    assert stored is not None
    assert stored["base_url"] == "https://api.deepseek.com"
    assert stored["model"] == "deepseek-flash"
    assert stored["keys"] == [DS_KEY, second]


# --------------------------------------------------------------------------- #
# OmniRoute: import only when a configuration is detected
# --------------------------------------------------------------------------- #
def test_omniroute_import_copies_the_gateway_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv = tmp_path / "omniroute.env"
    dotenv.write_text(
        "BASE_URL=http://127.0.0.1:20128\n" f"OMNIROUTE_API_KEY={OMNI_KEY}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UH_OMNIROUTER_CONFIG", str(dotenv))
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            state = (await http.get("/api/providers", headers=AUTH)).json()["keys"]
            assert state["omniroute"]["detected"] is True
            assert state["omniroute"]["has_key"] is True
            assert state["omniroute"]["base_url"] == "http://127.0.0.1:20128"

            response = await http.post(
                "/api/providers/omniroute/import", headers=AUTH
            )
            assert response.status_code == 200
            body = response.json()
            assert body["imported"] is True and body["name"] == "omnirouter"
            assert body["key_hints"] == [f"omn…{OMNI_KEY[-4:]}"]
            assert_no_full_key(response.text, OMNI_KEY)

    run(scenario())
    stored = keys_mod.get_provider("omnirouter")
    assert stored is not None
    assert stored["base_url"] == "http://127.0.0.1:20128"
    assert stored["keys"] == [OMNI_KEY]


def test_omniroute_import_without_a_configuration_is_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            response = await http.post("/api/providers/omniroute/import", headers=AUTH)
            assert response.status_code == 404
            assert "OmniRoute" in response.json()["detail"]

    run(scenario())
    assert keys_mod.get_provider("omnirouter") is None


# --------------------------------------------------------------------------- #
# the GET endpoint itself
# --------------------------------------------------------------------------- #
def test_a_stored_key_never_appears_in_any_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, monkeypatch)

    async def scenario():
        async with client(app) as http:
            await http.post(
                "/api/providers/cloudflare/config",
                json={"account_id": ACCOUNT, "token": CF_TOKEN},
                headers=AUTH,
            )
            await http.post("/api/providers/deepseek/key", json={"key": DS_KEY}, headers=AUTH)
            for path in ("/api/providers", "/api/status", "/api/models"):
                response = await http.get(path, headers=AUTH)
                assert_no_full_key(response.text, CF_TOKEN, DS_KEY)

    run(scenario())
