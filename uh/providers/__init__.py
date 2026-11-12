"""Provider layer: one ``chat`` call, optionally streamed, plus the registry.

Built-in providers:

* ``ollama``      - local models, ``/api/chat`` (NDJSON streaming);
* ``omnirouter``  - a local OmniRoute gateway speaking the OpenAI API
  (``/v1/chat/completions``, SSE streaming);
* ``cloudflare``  - Cloudflare Workers AI, OpenAI-compatible, configured by
  ``UH_CLOUDFLARE_ACCOUNT_ID`` + a token (environment or the web panel);
* ``deepseek``    - DeepSeek, OpenAI-compatible at ``https://api.deepseek.com``
  (``/chat/completions``), the paid fallback.

``build_provider(kind, model)`` is the only construction path used by the CLI,
so adding a provider means adding one module and one registry entry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List, Optional


class ProviderError(RuntimeError):
    """Any provider failure: network, HTTP status, malformed answer, missing model.

    Deliberately distinct from :class:`uh.agent.BudgetExhausted`: callers must be
    able to tell "the model is unreachable" from "the model kept calling tools".

    ``status`` is the HTTP status when the failure was an answer (429, 401, …):
    the fallback log line reports ``reason`` - a classified, one-line reason -
    instead of the raw message, which can quote the request body.
    """

    def __init__(self, message: str, *, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def reason(self) -> str:
        """Short classified reason for fallback lines: ``429 rate limit``."""
        if self.status is not None:
            from ..router.client import classify_status

            return f"{self.status} {classify_status(self.status).replace('_', ' ')}"
        return type(self).__name__


class Provider(ABC):
    """Base class for every provider (local or remote)."""

    name = "base"

    @abstractmethod
    def chat(
        self,
        messages: List[dict],
        tools: List[dict],
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> dict:
        """Return one assistant message dict (``role``/``content``/``tool_calls``).

        ``on_token`` is ``None`` for a blocking call; otherwise each text delta is
        passed to it and the complete message is returned at the end.
        """
        raise NotImplementedError


# Imported after Provider/ProviderError exist: the submodules import them back
# from this package, which works because those names are already bound.
from .cloudflare import CloudflareProvider  # noqa: E402
from .deepseek import DeepSeekProvider  # noqa: E402
from .ollama import OllamaProvider  # noqa: E402
from .omnirouter import OmniRouterProvider  # noqa: E402
from .openai_compat import OpenAICompatProvider  # noqa: E402


def _build_router(model: str = "", **kwargs) -> "Provider":
    """Free-provider router. Imported lazily: uh.router imports this module."""
    from ..router.provider import RouterProvider

    return RouterProvider(model, **kwargs)


PROVIDERS: dict[str, Callable[..., Provider]] = {
    "ollama": OllamaProvider,
    "omnirouter": OmniRouterProvider,
    "cloudflare": CloudflareProvider,
    "deepseek": DeepSeekProvider,
    "router": _build_router,
}


def available_providers() -> List[str]:
    return sorted(PROVIDERS)


def configured_providers() -> List[str]:
    """Providers the user added with ``uh providers add`` (keys.toml)."""
    from ..keys import provider_names

    return provider_names()


def all_provider_kinds() -> List[str]:
    return sorted(set(PROVIDERS) | set(configured_providers()))


def build_provider(kind: str, model: str = "", **kwargs) -> Provider:
    """Instantiate the provider named by ``kind`` or raise a listed ``ProviderError``."""
    key = (kind or "").strip().lower()
    if key in PROVIDERS:
        factory = PROVIDERS[key]
        try:
            return factory(model, **kwargs)
        except ProviderError:
            raise
        except TypeError as exc:
            raise ProviderError(f"cannot build provider '{key}': {exc}") from exc

    # A user-configured OpenAI-compatible gateway: same wire format, own key.
    from ..keys import get_provider

    entry = get_provider(key)
    if entry:
        return OpenAICompatProvider(
            model=model or entry.get("model", ""),
            base_url=entry.get("base_url", ""),
            api_key=entry.get("key", ""),
            label=key,
        )

    raise ProviderError(
        f"unknown provider '{kind}'; available: {', '.join(all_provider_kinds())} "
        "(add one with: uh providers add <name>)"
    )
