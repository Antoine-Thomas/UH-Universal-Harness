"""Hugging Face provider: TGI in front, the HF router behind.

Two transports, one surface. Both speak ``POST /v1/chat/completions``:

* **TGI local** (``http://127.0.0.1:8080``) - a model served on this machine, no
  account, no network. Tried first: a user who launched a local server meant it.
* **The HF router** (``https://router.huggingface.co/v1``) - the current
  OpenAI-compatible gateway, authenticated with a token.

Deliberately *not* ``https://api-inference.huggingface.co/models/<model>``: that is
the legacy per-task API (raw text generation, no chat roles, no streaming of
tokens), and pointing a chat loop at it produces a provider that looks configured
and fails on the first turn.

Key resolution, first hit wins: ``UH_HUGGINGFACE_API_KEY``, ``HF_TOKEN``,
``HUGGINGFACE_API_KEY``, then ``[providers.huggingface]`` in ``$UH_HOME/keys.toml``.
A local TGI needs no key at all - that is the point of running it locally.
"""

from __future__ import annotations

import os
import socket
from typing import Optional
from urllib.parse import urlparse

from . import ProviderError
from .omnirouter import OmniRouterProvider

BASE_URL_ROUTER = "https://router.huggingface.co/v1"
BASE_URL_TGI = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"
CHAT_PATH = "/chat/completions"
ENV_API_KEY = "UH_HUGGINGFACE_API_KEY"
VENDOR_ENV_KEYS = ("HF_TOKEN", "HUGGINGFACE_API_KEY")
ENV_BASE_URL = "UH_HUGGINGFACE_BASE_URL"
DEFAULT_TIMEOUT = 180.0


def _is_loopback(base: str) -> bool:
    host = (urlparse(base).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _reachable(url: str, timeout: float = 0.25) -> bool:
    """True when something accepts a TCP connection at ``url``'s host:port."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def resolve_base_url(explicit: Optional[str] = None) -> str:
    """Explicit, then the environment, then a local TGI, then the router.

    The probe is a socket connection, not an HTTP call: asking a server that is not
    there costs a timeout on every provider build, and building a provider happens
    on every status refresh.
    """
    if explicit:
        return explicit.rstrip("/")
    from_env = os.environ.get(ENV_BASE_URL, "").strip()
    if from_env:
        return from_env.rstrip("/")
    if _reachable(BASE_URL_TGI):
        return BASE_URL_TGI
    return BASE_URL_ROUTER


def resolve_api_key() -> Optional[str]:
    for name in (ENV_API_KEY, *VENDOR_ENV_KEYS):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    from ..keys import get_provider

    entry = get_provider("huggingface") or {}
    stored = [item for item in (entry.get("keys") or []) if item]
    return stored[0] if stored else None


class HuggingFaceProvider(OmniRouterProvider):
    """Hugging Face chat: local TGI first, the router otherwise."""

    name = "huggingface"
    label = "Hugging Face"
    chat_path = CHAT_PATH

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        resolved = resolve_base_url(base_url)
        key = (api_key or resolve_api_key() or "").strip()
        local = _is_loopback(resolved)
        if not key and not local:
            # A remote router call without a token answers 401 halfway through a
            # turn. Say it before the first token, and say where to put the token.
            raise ProviderError(
                "Hugging Face needs a token for the remote router: set "
                "HUGGINGFACE_API_KEY (or HF_TOKEN), or run a local TGI server on "
                f"{BASE_URL_TGI} - a local server needs no account"
            )
        # The parent resolves nothing of its own when both are given: it stores
        # exactly what it is handed.
        super().__init__(model=model or DEFAULT_MODEL, base_url=resolved, api_key=key, timeout=timeout)
        self.local = local
