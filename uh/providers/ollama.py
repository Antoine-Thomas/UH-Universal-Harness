"""Ollama provider over httpx (no extra dependency, no API key).

Endpoints used:
  * ``POST /api/chat``  - one blocking turn, ``stream: false`` (Jalon 1);
  * ``GET  /api/tags``   - model discovery for ``uh model list`` and the chat
    default model.

Base URL: ``UH_OLLAMA_URL``, default ``http://127.0.0.1:11434``.
Every failure surfaces as :class:`ProviderError` - never as a raw httpx error.
"""

from __future__ import annotations

import os
from typing import List

import httpx

from . import Provider, ProviderError

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 120.0
DISCOVERY_TIMEOUT = 5.0


def base_url() -> str:
    return (os.environ.get("UH_OLLAMA_URL") or DEFAULT_BASE_URL).rstrip("/")


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
        self.model = model
        self.base_url = (base_url_override or base_url()).rstrip("/")
        self.timeout = timeout

    def chat(self, messages: List[dict], tools: List[dict]) -> dict:
        payload: dict = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools

        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"cannot reach Ollama at {self.base_url} ({type(exc).__name__}: {exc}); "
                "start it with `ollama serve`"
            ) from exc

        if response.status_code != 200:
            body = (response.text or "").strip()[:300]
            raise ProviderError(f"Ollama HTTP {response.status_code}: {body}")

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"Ollama returned non-JSON body: {exc}") from exc

        if isinstance(data, dict) and data.get("error"):
            raise ProviderError(f"Ollama error: {data['error']}")

        message = data.get("message") if isinstance(data, dict) else None
        if not isinstance(message, dict):
            raise ProviderError("Ollama response has no 'message' object")

        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
        }


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
