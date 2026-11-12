"""Cloudflare Workers AI provider: construction, config resolution, wire seam.

No real network: httpx.post / httpx.stream are monkeypatched. The token used
here is a dummy marker asserted to travel in the Authorization header only.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from uh import config as uh_config
from uh.providers import PROVIDERS, ProviderError, build_provider
from uh.providers.cloudflare import (
    API_ROOT,
    CloudflareProvider,
    resolve_account_id,
)

DUMMY_TOKEN = "cf-test-token-not-a-real-one"
ACCOUNT = "abc123def456"


@pytest.fixture
def cloudflare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("UH_CLOUDFLARE_API_TOKEN", DUMMY_TOKEN)


@pytest.fixture
def no_cloudflare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("UH_CLOUDFLARE_API_TOKEN", raising=False)


def test_builds_from_environment(cloudflare_env: None) -> None:
    provider = CloudflareProvider()
    assert provider.name == "cloudflare"
    assert provider.base_url == f"{API_ROOT}/{ACCOUNT}/ai"
    assert provider.model == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert provider.api_key == DUMMY_TOKEN


def test_registered_and_buildable(cloudflare_env: None) -> None:
    assert "cloudflare" in PROVIDERS
    provider = build_provider("cloudflare", "@cf/meta/llama-3.1-8b-instruct")
    assert isinstance(provider, CloudflareProvider)
    assert provider.model == "@cf/meta/llama-3.1-8b-instruct"


def test_missing_account_id_raises_naming_the_variable(no_cloudflare_env: None) -> None:
    with pytest.raises(ProviderError, match="UH_CLOUDFLARE_ACCOUNT_ID"):
        CloudflareProvider()


def test_missing_token_raises_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, no_cloudflare_env: None
) -> None:
    monkeypatch.setenv("UH_CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    with pytest.raises(ProviderError, match="UH_CLOUDFLARE_API_TOKEN"):
        CloudflareProvider()


def test_account_id_falls_back_to_config_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_cloudflare_env: None
) -> None:
    monkeypatch.setenv("UH_CLOUDFLARE_API_TOKEN", DUMMY_TOKEN)
    home = tmp_path / "uh-home"
    (home / "config.toml").write_text(
        'cloudflare_account_id = "from-config"\n', encoding="utf-8"
    )
    assert resolve_account_id() == "from-config"
    assert CloudflareProvider().base_url == f"{API_ROOT}/from-config/ai"


def test_env_account_id_beats_config(
    monkeypatch: pytest.MonkeyPatch, cloudflare_env: None
) -> None:
    monkeypatch.setattr(
        uh_config, "load", lambda: uh_config.Config(values={"cloudflare_account_id": "x"})
    )
    assert resolve_account_id() == ACCOUNT


def test_token_has_no_config_key() -> None:
    """A credential never lands in the flat config: no key may accept it."""
    for key in uh_config.KEY_TYPES:
        last = key.rsplit("_", 1)[-1]
        assert last not in {"token", "secret", "password", "key"}, key


def test_chat_posts_to_the_openai_compatible_url(
    monkeypatch: pytest.MonkeyPatch, cloudflare_env: None
) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "bonjour", "tool_calls": None}}
                ]
            }

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    message = CloudflareProvider().chat([{"role": "user", "content": "salut"}], [])
    assert message["content"] == "bonjour"
    assert captured["url"] == (
        f"{API_ROOT}/{ACCOUNT}/ai/v1/chat/completions"
    )
    assert captured["headers"]["Authorization"] == f"Bearer {DUMMY_TOKEN}"
    assert captured["json"]["stream"] is False


def test_chat_streams_sse_deltas(
    monkeypatch: pytest.MonkeyPatch, cloudflare_env: None
) -> None:
    lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
    ]

    class FakeStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_lines(self):
            return iter(lines)

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream())
    tokens: list[str] = []
    message = CloudflareProvider().chat(
        [{"role": "user", "content": "hi"}], [], on_token=tokens.append
    )
    assert tokens == ["Hello", " world"]
    assert message["content"] == "Hello world"
