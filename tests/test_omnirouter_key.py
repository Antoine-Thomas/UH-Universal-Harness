"""OmniRoute: where the key comes from, and why "it never worked".

Measured on the operator's machine (16/11/2026):

* `~/.omniroute/.env` carried a 32-character key that answers **200** on
  `/v1/chat/completions` but **401** on `/v1/models` (a restricted client key);
* the master key lived in `%LOCALAPPDATA%\\hermes\\.env` and in UH's own store,
  which the provider never read.

So UH listed no model, then answered 401 - while the same route worked from
Hermes. The provider now reads UH's store before the gateway's dotenv.
"""

from __future__ import annotations

import httpx
import pytest

from uh import keys as uh_keys
from uh.providers import ProviderError
from uh.providers import omnirouter as om


@pytest.fixture()
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("UH_OMNIROUTER_API_KEY", "OMNIROUTE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_an_explicit_environment_wins(monkeypatch: pytest.MonkeyPatch, no_env: None) -> None:
    monkeypatch.setenv("UH_OMNIROUTER_API_KEY", "from-env")
    monkeypatch.setattr(uh_keys, "get_provider", lambda name: {"key": "from-store"})
    monkeypatch.setattr(om, "_dotenv", lambda: {"OMNIROUTE_API_KEY": "from-file"})

    assert om.resolve_api_key() == "from-env"


def test_the_key_typed_in_the_panel_beats_the_gateways_file(
    monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    """The one defect that made OmniRoute look dead: the panel's key was ignored."""
    monkeypatch.setattr(uh_keys, "get_provider", lambda name: {"key": "from-store"})
    monkeypatch.setattr(om, "_dotenv", lambda: {"OMNIROUTE_API_KEY": "stale-file"})

    assert om.resolve_api_key() == "from-store"


def test_the_dotenv_is_the_last_resort(monkeypatch: pytest.MonkeyPatch, no_env: None) -> None:
    monkeypatch.setattr(uh_keys, "get_provider", lambda name: None)
    monkeypatch.setattr(om, "_dotenv", lambda: {"OMNIROUTE_API_KEY": "from-file"})

    assert om.resolve_api_key() == "from-file"


def test_a_broken_key_store_does_not_hide_the_rest(
    monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    def boom(name: str):
        raise RuntimeError("keys.toml unreadable")

    monkeypatch.setattr(uh_keys, "get_provider", boom)
    monkeypatch.setattr(om, "_dotenv", lambda: {"OMNIROUTE_API_KEY": "from-file"})

    assert om.resolve_api_key() == "from-file"


def test_a_401_names_where_the_key_comes_from(
    monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Authentication required"}})

    provider = om.OmniRouterProvider(model="eco", base_url="http://127.0.0.1:20128", api_key="k")
    provider.transport_error = None  # no-op, keeps the signature readable
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: httpx.Response(401, json={"error": {"message": "no"}})
    )
    with pytest.raises(ProviderError) as caught:
        provider.chat([{"role": "user", "content": "x"}], [])

    message = str(caught.value)
    assert caught.value.status == 401
    assert "panneau" in message, "the message names the control that fixes it"
    assert "omniroute/.env" in message, "and the file that may be stale"


def test_a_401_on_the_model_listing_is_explained_too(
    monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    monkeypatch.setattr(om, "resolve_api_key", lambda: "k")
    monkeypatch.setattr(om, "resolve_base_url", lambda: "http://127.0.0.1:20128")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: httpx.Response(401, json={}))

    with pytest.raises(ProviderError) as caught:
        om.list_remote_models()

    assert "panneau" in str(caught.value), "a dead model picker says why"
