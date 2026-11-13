"""Ollama provider over httpx: blocking or streamed chat, model discovery, pull.

Endpoints used:
  * ``POST /api/chat``  - one turn; ``stream: false`` for blocking, ``stream: true``
    (NDJSON) when ``on_token`` is given;
  * ``GET  /api/tags``  - model discovery for ``uh model list`` / default model;
  * ``POST /api/pull``  - model download, NDJSON progress events.

Base URL: ``UH_OLLAMA_URL``, default ``http://127.0.0.1:11434``.
Every failure surfaces as :class:`ProviderError` - never as a raw httpx error.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Iterator, List, Optional

import httpx

from . import Provider, ProviderError, request_messages

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
#: A local 8B model reading a few thousand characters can take minutes on a
#: laptop GPU/CPU mix; 120 s cut it off mid-answer and the pass lost the turn.
DEFAULT_TIMEOUT = 300.0
DISCOVERY_TIMEOUT = 5.0
PULL_READ_TIMEOUT = 300.0


def base_url() -> str:
    return (os.environ.get("UH_OLLAMA_URL") or DEFAULT_BASE_URL).rstrip("/")


def _tool_calls_from(message: dict) -> List[dict]:
    calls = message.get("tool_calls") or []
    return [call for call in calls if isinstance(call, dict)]


def chat_options(params: dict) -> dict:
    """Ollama's ``options`` block from the session's parameters.

    Only the keys ``/api/chat`` documents, and ``max_tokens`` becomes
    ``num_predict``: a field Ollama does not know is not "ignored", it is a 400.
    """
    options: dict = {}
    for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
        if key in params:
            options[key] = params[key]
    if "max_tokens" in params:
        options["num_predict"] = int(params["max_tokens"])
    return options


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        base_url_override: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not model:
            raise ProviderError("a model name is required (uh chat --model <name>)")
        super().__init__()
        self.model = model
        self.base_url = (base_url_override or base_url()).rstrip("/")
        self.timeout = timeout

    # -- chat -------------------------------------------------------------- #
    def chat(
        self,
        messages: List[dict],
        tools: List[dict],
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": request_messages(messages),
            "stream": on_token is not None,
        }
        if tools:
            payload["tools"] = tools
        options = chat_options(getattr(self, "request_params", {}) or {})
        if options:
            payload["options"] = options

        if on_token is None:
            return self._chat_blocking(payload)
        return self._chat_streaming(payload, on_token)

    def _chat_blocking(self, payload: dict) -> dict:
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

        if response.status_code != 200:
            raise self._http_error(response.status_code, response.text or "")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"Ollama returned non-JSON body: {exc}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"Ollama error: {data['error']}")

        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            raise ProviderError("Ollama response has no 'message' object")

        # Ollama reports prompt_eval_count / eval_count at the top level, and the
        # model it actually served (an alias may resolve to another tag).
        self._record_meta(model=data.get("model"), usage=data)

        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": _tool_calls_from(message),
        }

    def _chat_streaming(self, payload: dict, on_token: Callable[[str], None]) -> dict:
        parts: List[str] = []
        tool_calls: List[dict] = []
        final: dict = {}
        try:
            with httpx.stream(
                "POST", f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            ) as response:
                if response.status_code != 200:
                    raise self._http_error(
                        response.status_code, "".join(response.iter_text())
                    )
                for line in response.iter_lines():
                    event = _parse_ndjson(line)
                    if event is None:
                        continue
                    if event.get("error"):
                        raise ProviderError(f"Ollama error: {event['error']}")
                    message = event.get("message") or {}
                    if isinstance(message, dict):
                        delta = message.get("content") or ""
                        if delta:
                            parts.append(delta)
                            on_token(delta)
                        calls = _tool_calls_from(message)
                        if calls:  # Ollama sends the complete list in the final chunk
                            tool_calls = calls
                    if event.get("done"):
                        final = event
                        break
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

        # The final NDJSON event carries prompt_eval_count / eval_count.
        self._record_meta(model=final.get("model"), usage=final)
        return {"role": "assistant", "content": "".join(parts), "tool_calls": tool_calls}

    def _http_error(self, status: int, body: str) -> ProviderError:
        """Turn Ollama's "this model cannot call tools" 400 into an instruction.

        tinyllama and friends reject the ``tools`` array outright. The raw body
        (``{"error":{"message":"registry.ollama.ai/library/tinyllama:latest does
        not support tools"}}``) tells a user nothing actionable, and the fallback
        line would only say "400 bad request". The message is rewritten and a
        short ``hint`` rides on the classified reason.
        """
        text = (body or "").strip()
        if status == 400 and "does not support tools" in text.lower():
            return ProviderError(
                f"model '{self.model}' does not support tool calling; "
                "try a different model or disable tools.",
                status=400,
                hint=f"model '{self.model}' does not support tool calling",
            )
        return ProviderError(f"Ollama HTTP {status}: {text[:300]}", status=status)

    def _unreachable(self, exc: Exception) -> ProviderError:
        return ProviderError(
            f"cannot reach Ollama at {self.base_url} ({type(exc).__name__}: {exc}); "
            "start it with `ollama serve`"
        )


# --------------------------------------------------------------------------- #
# discovery and pull
# --------------------------------------------------------------------------- #
def list_models(base_url_override: str | None = None, timeout: float = DISCOVERY_TIMEOUT) -> List[dict]:
    """Return ``[{"name", "size", "modified_at"}]`` from ``GET /api/tags``."""
    root = (base_url_override or base_url()).rstrip("/")
    try:
        response = httpx.get(f"{root}/api/tags", timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"cannot reach Ollama at {root} ({type(exc).__name__}: {exc}); "
            "start it with `ollama serve`"
        ) from exc

    if response.status_code != 200:
        raise ProviderError(f"Ollama HTTP {response.status_code} on /api/tags")

    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(f"Ollama returned non-JSON body on /api/tags: {exc}") from exc

    raw = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise ProviderError("unexpected /api/tags payload: missing 'models' list")

    models: List[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or entry.get("model")
        if not name:
            continue
        models.append(
            {
                "name": str(name),
                "size": int(entry.get("size") or 0),
                "modified_at": str(entry.get("modified_at") or ""),
            }
        )
    return models


def pull_model(
    name: str,
    on_event: Callable[[dict], None],
    base_url_override: str | None = None,
    timeout: float = PULL_READ_TIMEOUT,
) -> None:
    """``POST /api/pull`` and forward every NDJSON event to ``on_event``.

    Raises ``ProviderError`` on transport failure, bad status or an ``error``
    event. The size of a remote model is NOT known before the download
    (``/api/show`` returns 404 for a non-local model), so no client-side
    size guard is possible: the caller decides about confirmation.
    """
    root = (base_url_override or base_url()).rstrip("/")
    limits = httpx.Timeout(timeout, connect=10.0)
    try:
        with httpx.stream(
            "POST", f"{root}/api/pull", json={"name": name, "stream": True}, timeout=limits
        ) as response:
            if response.status_code != 200:
                body = "".join(response.iter_text())[:300]
                raise ProviderError(f"Ollama HTTP {response.status_code} on /api/pull: {body}")
            for line in response.iter_lines():
                event = _parse_ndjson(line)
                if event is None:
                    continue
                if event.get("error"):
                    raise ProviderError(f"Ollama error: {event['error']}")
                on_event(event)
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"cannot reach Ollama at {root} ({type(exc).__name__}: {exc}); "
            "start it with `ollama serve`"
        ) from exc


def _parse_ndjson(line: str) -> dict | None:
    text = (line or "").strip()
    if not text:
        return None
    try:
        event = json.loads(text)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def iter_pull_events(name: str, base_url_override: str | None = None) -> Iterator[dict]:
    """Generator wrapper around :func:`pull_model` (handy for tests)."""
    events: List[dict] = []
    pull_model(name, events.append, base_url_override=base_url_override)
    yield from events
