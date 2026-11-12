"""DeepSeek provider: OpenAI-compatible endpoint, SSE streaming.

Measured on the vendor's own API documentation (2026-11-12):

* base URL ``https://api.deepseek.com``, endpoint ``POST /chat/completions``
  - **no ``/v1`` prefix** in the documented form, hence ``chat_path`` (the
  inherited transport would otherwise append ``/v1/chat/completions``);
* models ``deepseek-flash`` (served by V4.1-Flash) and ``deepseek-v4-pro``;
  the legacy names ``deepseek-chat`` / ``deepseek-reasoner`` were retired on
  2026-07-24.

Key resolution, first hit wins: ``UH_DEEPSEEK_API_KEY``, then the vendor's own
``DEEPSEEK_API_KEY``, then ``[providers.deepseek]`` in ``$UH_HOME/keys.toml``
(0600, written by the API-key panel). Never ``config.toml``; the key is only
ever sent as a header and never printed.
"""

from __future__ import annotations

import os
from typing import Optional

from . import ProviderError
from .omnirouter import OmniRouterProvider

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
CHAT_PATH = "/chat/completions"
ENV_API_KEY = "UH_DEEPSEEK_API_KEY"
VENDOR_ENV_API_KEY = "DEEPSEEK_API_KEY"
DEFAULT_TIMEOUT = 120.0


def resolve_api_key() -> Optional[str]:
    """Environment first, then the key the web panel stored in ``keys.toml``."""
    for name in (ENV_API_KEY, VENDOR_ENV_API_KEY):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    from ..keys import get_provider

    entry = get_provider("deepseek") or {}
    stored = [item for item in (entry.get("keys") or []) if item]
    return stored[0] if stored else None


class DeepSeekProvider(OmniRouterProvider):
    """DeepSeek over its OpenAI-compatible endpoint (the paid fallback)."""

    name = "deepseek"
    label = "DeepSeek"
    chat_path = CHAT_PATH

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: str = BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        key = (api_key or resolve_api_key() or "").strip()
        if not key:
            raise ProviderError(
                f"DeepSeek not configured: set {ENV_API_KEY} or save a key "
                "in the API-key panel (`uh web`)"
            )
        super().__init__(
            model=model or DEFAULT_MODEL,
            base_url=base_url,
            api_key=key,
            timeout=timeout,
        )


__all__ = [
    "DeepSeekProvider",
    "resolve_api_key",
    "BASE_URL",
    "DEFAULT_MODEL",
    "CHAT_PATH",
    "ENV_API_KEY",
]
