"""A 401 from OmniRoute must name what actually failed.

OmniRoute answers 401 for two unrelated reasons:

* its own API key was refused (``invalid_api_key``) — the key files are the
  answer;
* the requested model names an upstream provider OmniRoute holds no credentials
  for (``provider_not_configured``) — the key is fine, the *model id* is wrong.

Measured 2026-11-17: ``uh chat --provider omnirouter`` inherited
``default_model = deepseek-flash``, OmniRoute answered
``{"code": "provider_not_configured", "message": "No active credentials for
provider: deepseek."}`` and UH reported "clé OmniRoute refusée". Hours went into
auditing a valid key. Blaming the key is only correct in the first case.
"""

from __future__ import annotations

import httpx
import pytest

from uh.providers import ProviderError
from uh.providers import omnirouter as om

CHAT_URL = "http://127.0.0.1:20128/v1/chat/completions"
MODELS_URL = "http://127.0.0.1:20128/v1/models"

PROVIDER_NOT_CONFIGURED = {
    "error": {
        "message": "No active credentials for provider: deepseek.",
        "type": "authentication_error",
        "code": "provider_not_configured",
    }
}
INVALID_API_KEY = {
    "error": {
        "message": "Invalid API key",
        "type": "invalid_api_key",
        "code": "invalid_api_key",
    }
}


def _response(status: int, payload: dict, url: str = CHAT_URL) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


def _provider() -> om.OmniRouterProvider:
    return om.OmniRouterProvider(
        model="deepseek-flash", base_url="http://127.0.0.1:20128", api_key="test-key-0123456789"
    )


def test_missing_upstream_credentials_are_not_reported_as_a_refused_key() -> None:
    with pytest.raises(ProviderError) as failure:
        _provider()._payload(_response(401, PROVIDER_NOT_CONFIGURED))

    message = str(failure.value)
    assert "provider_not_configured" in message
    assert "deepseek" in message  # names the upstream that has no credentials
    assert "clé OmniRoute refusée" not in message
    assert failure.value.status == 401


def test_a_refused_key_still_points_at_the_key_files() -> None:
    with pytest.raises(ProviderError) as failure:
        _provider()._payload(_response(401, INVALID_API_KEY))

    message = str(failure.value)
    assert "clé OmniRoute refusée" in message
    assert "uh keys add omnirouter" in message
    assert failure.value.status == 401


def test_a_403_with_an_unreadable_body_keeps_the_key_message() -> None:
    response = httpx.Response(403, text="<html>nope</html>", request=httpx.Request("POST", CHAT_URL))

    with pytest.raises(ProviderError) as failure:
        _provider()._payload(response)

    assert "clé OmniRoute refusée" in str(failure.value)


def test_model_listing_reports_the_missing_upstream_credentials_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        om.httpx, "get", lambda *args, **kwargs: _response(401, PROVIDER_NOT_CONFIGURED, MODELS_URL)
    )

    with pytest.raises(ProviderError) as failure:
        om.list_remote_models(base_url="http://127.0.0.1:20128", api_key="test-key-0123456789")

    message = str(failure.value)
    assert "provider_not_configured" in message
    assert "clé OmniRoute refusée" not in message
