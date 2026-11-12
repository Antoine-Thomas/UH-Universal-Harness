"""Cloudflare Workers AI provider: OpenAI-compatible endpoint, SSE streaming.

Cloudflare exposes an OpenAI-compatible surface per account
(``https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1`` - the
inherited transport appends ``/v1/chat/completions``), so this provider only
resolves its configuration and lets :class:`OmniRouterProvider` do the wire
work.

Configuration resolution:

* **account id**: ``UH_CLOUDFLARE_ACCOUNT_ID``, then the
  ``cloudflare_account_id`` key of ``config.toml`` (not a credential).
* **API token**: ``UH_CLOUDFLARE_API_TOKEN`` and **nowhere else** - a token
  is never written to ``config.toml`` (flat config holds no secrets; user
  keys live in ``keys.toml``, and this one is not a user-catalogue entry).

Both are required; a missing one raises :class:`ProviderError` at construction
naming the variable - a missing credential costs a skip, never a 401.

Default model: ``@cf/meta/llama-3.3-70b-instruct-fp8-fast``, listed with
function calling on the Cloudflare Workers AI documentation (checked
2026-11-12). Override with ``uh chat -p cloudflare -m <model>``.
"""

from __future__ import annotations

import os
from typing import Optional

from . import ProviderError
from .omnirouter import OmniRouterProvider

API_ROOT = "https://api.cloudflare.com/client/v4/accounts"
DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
DEFAULT_TIMEOUT = 120.0
ENV_ACCOUNT_ID = "UH_CLOUDFLARE_ACCOUNT_ID"
ENV_API_TOKEN = "UH_CLOUDFLARE_API_TOKEN"


def resolve_account_id() -> Optional[str]:
    """``UH_CLOUDFLARE_ACCOUNT_ID``, then ``cloudflare_account_id`` in config."""
    value = os.environ.get(ENV_ACCOUNT_ID, "").strip()
    if value:
        return value
    from .. import config as uh_config

    configured = str(uh_config.load().get("cloudflare_account_id") or "").strip()
    return configured or None


def resolve_api_token() -> Optional[str]:
    """The token is read from the environment only - never from a file UH writes."""
    value = os.environ.get(ENV_API_TOKEN, "").strip()
    return value or None


class CloudflareProvider(OmniRouterProvider):
    """Cloudflare Workers AI over its OpenAI-compatible endpoint."""

    name = "cloudflare"
    label = "Cloudflare Workers AI"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        account_id: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        account = (account_id or resolve_account_id() or "").strip()
        if not account:
            raise ProviderError(
                f"Cloudflare not configured: set {ENV_ACCOUNT_ID} "
                "(or `uh config set cloudflare_account_id <id>`)"
            )
        token = (api_token or resolve_api_token() or "").strip()
        if not token:
            raise ProviderError(
                f"Cloudflare not configured: set {ENV_API_TOKEN} "
                "(environment variable only - a token never goes to config.toml)"
            )
        super().__init__(
            model=model or DEFAULT_MODEL,
            base_url=f"{API_ROOT}/{account}/ai",
            api_key=token,
            timeout=timeout,
        )


__all__ = [
    "CloudflareProvider",
    "resolve_account_id",
    "resolve_api_token",
    "API_ROOT",
    "DEFAULT_MODEL",
    "ENV_ACCOUNT_ID",
    "ENV_API_TOKEN",
]
