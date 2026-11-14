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
from typing import Callable, Dict, List, Optional


class ProviderError(RuntimeError):
    """Any provider failure: network, HTTP status, malformed answer, missing model.

    Deliberately distinct from :class:`uh.agent.BudgetExhausted`: callers must be
    able to tell "the model is unreachable" from "the model kept calling tools".

    ``status`` is the HTTP status when the failure was an answer (429, 401, …):
    the fallback log line reports ``reason`` - a classified, one-line reason -
    instead of the raw message, which can quote the request body.
    """

    def __init__(self, message: str, *, status: Optional[int] = None, hint: str = "") -> None:
        super().__init__(message)
        self.status = status
        # One clean clause, safe to print: never a payload excerpt. It rides on
        # the classified ``reason`` line so a fallback explains itself without
        # quoting the request.
        self.hint = hint

    @property
    def reason(self) -> str:
        """Short classified reason for fallback lines: ``429 rate limit``."""
        if self.status is not None:
            from ..router.client import classify_status

            base = f"{self.status} {classify_status(self.status).replace('_', ' ')}"
            return f"{base}: {self.hint}" if self.hint else base
        return type(self).__name__


def request_messages(messages: List[dict]) -> List[dict]:
    """Copy of ``messages`` with an empty ``tool_calls`` list removed.

    OpenAI tolerates ``"tool_calls": []`` on an assistant message; DeepSeek does
    not, and rejects the *next* turn with ``400 Invalid 'messages[N].tool_calls':
    empty array`` because the assistant message is replayed from history. The
    wire payload is fixed here, once, for every OpenAI-compatible provider; the
    session keeps its own dicts untouched.
    """
    cleaned: List[dict] = []
    for message in messages:
        if isinstance(message, dict) and "tool_calls" in message and not message["tool_calls"]:
            message = {key: value for key, value in message.items() if key != "tool_calls"}
        cleaned.append(message)
    return cleaned


USAGE_KEYS = ("prompt", "completion", "total")


def normalise_usage(raw: object) -> Optional[dict]:
    """``{"prompt", "completion", "total"}`` from OpenAI or Ollama counters.

    OpenAI: ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens``.
    Ollama: ``prompt_eval_count`` / ``eval_count`` (no total).
    ``None`` when the provider reported nothing, so "not reported" stays
    distinguishable from a real zero.
    """
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens", raw.get("prompt_eval_count"))
    completion = raw.get("completion_tokens", raw.get("eval_count"))
    total = raw.get("total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    try:
        prompt = int(prompt or 0)
        completion = int(completion or 0)
        total = int(total) if total is not None else prompt + completion
    except (TypeError, ValueError):
        return None
    return {"prompt": prompt, "completion": completion, "total": total or prompt + completion}


class Provider(ABC):
    """Base class for every provider (local or remote)."""

    name = "base"

    # Usage reporting is optional: a provider that cannot report counters leaves
    # these at their defaults and the console prints "tokens non rapportés".
    last_real_model: Optional[str] = None
    last_usage: Optional[dict] = None

    def __init__(self) -> None:
        self.last_real_model = None
        self.last_usage = None
        self._usage_total: dict = {"prompt": 0, "completion": 0, "total": 0}
        # Chat parameters chosen for this conversation (temperature, top_p,
        # max_tokens, penalties, reasoning_effort). A provider reads only the keys
        # its API documents, so an unsupported one is ignored rather than sent and
        # rejected - a settings slider must never be able to break a turn.
        self.request_params: Dict[str, object] = {}

    def set_params(self, params: Optional[dict]) -> None:
        """Remember the parameters the operator set for this session."""
        self.request_params = dict(params or {})

    def usage_snapshot(self) -> dict:
        """Cumulative counters since construction (a turn takes a diff of this)."""
        total = getattr(self, "_usage_total", None)
        if not isinstance(total, dict):
            return {"prompt": 0, "completion": 0, "total": 0}
        return {key: int(total.get(key) or 0) for key in USAGE_KEYS}

    def _record_meta(self, *, model: object = None, usage: object = None) -> None:
        """Remember what really answered and what it cost on this call."""
        if model:
            self.last_real_model = str(model)
        parsed = normalise_usage(usage)
        if parsed is None:
            return
        self.last_usage = parsed
        total = getattr(self, "_usage_total", None)
        if not isinstance(total, dict):
            total = {"prompt": 0, "completion": 0, "total": 0}
            self._usage_total = total
        for key in USAGE_KEYS:
            total[key] = int(total.get(key) or 0) + parsed[key]

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
from .huggingface import HuggingFaceProvider  # noqa: E402
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
    "huggingface": HuggingFaceProvider,
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
