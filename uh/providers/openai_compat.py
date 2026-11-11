"""Generic OpenAI-compatible provider, configured by the user (Jalon 4).

A user adds one with ``uh providers add <name> --base-url ... --model ...``;
the key lands in ``$UH_HOME/keys.toml`` (mode 0600) and never touches the
repository. The transport is the same OpenAI surface the OmniRouter provider
already speaks (``/v1/chat/completions``, SSE streaming), so this class only
changes the labels and the configuration source - the parsing stays in one
place.
"""

from __future__ import annotations

from typing import Optional

from . import ProviderError
from .omnirouter import OmniRouterProvider


class OpenAICompatProvider(OmniRouterProvider):
    """Any OpenAI-compatible endpoint the user configured."""

    name = "openai-compat"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        label: Optional[str] = None,
        timeout: float = 120.0,
    ) -> None:
        if not base_url:
            raise ProviderError("OpenAI-compatible provider needs a base_url")
        self.base_url = base_url.rstrip("/")
        self.model = model or ""
        self.api_key = api_key or ""
        self.label = label or self.name
        self.timeout = timeout
