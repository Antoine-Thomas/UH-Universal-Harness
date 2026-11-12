"""OpenAI-compatible transport for the router. ``httpx`` is the only dependency.

Every failure is classified (``kind``) so the pool can decide: rotate the key,
cool the provider down, or give up. A stream that breaks *after* tokens were
already handed to the caller is flagged ``emitted=True`` - it cannot be retried
without duplicating text on screen.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

import httpx

from .catalog import ModelSpec, ProviderSpec


class RouterError(RuntimeError):
    """A failed call, classified well enough to pick the next candidate."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "error",
        status: Optional[int] = None,
        retry_after: Optional[float] = None,
        emitted: bool = False,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        self.emitted = emitted

    @property
    def retryable(self) -> bool:
        return self.kind in {"rate_limit", "server", "timeout", "transport", "stream_error"}


def _retry_after(response: httpx.Response) -> Optional[float]:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _classify(status: int) -> str:
    if status == 429:
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "payment"
    if status >= 500:
        return "server"
    return "error"


def _http_error(response: httpx.Response) -> RouterError:
    kind = _classify(response.status_code)
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or "")
            elif error:
                detail = str(error)
    except (ValueError, UnicodeDecodeError):
        detail = ""
    message = f"HTTP {response.status_code}" + (f": {detail[:200]}" if detail else "")
    return RouterError(
        message,
        kind=kind,
        status=response.status_code,
        retry_after=_retry_after(response),
    )


def _headers(spec: ProviderSpec, key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if key:
        if spec.auth == "x-api-key":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _body(model: str, messages: list[dict], tools: list[dict], stream: bool) -> dict:
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
    if tools:
        payload["tools"] = tools
    if stream:
        payload["stream_options"] = {"include_usage": False}
    return payload


def _merge_tool_call(store: dict, delta: dict) -> None:
    index = int(delta.get("index") or 0)
    slot = store.setdefault(index, {"function": {"name": "", "arguments": ""}})
    function = delta.get("function") or {}
    if function.get("name"):
        slot["function"]["name"] = function["name"]
    if function.get("arguments"):
        slot["function"]["arguments"] += function["arguments"]
    if delta.get("id"):
        slot["id"] = delta["id"]


def _assemble(content: str, calls: dict) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [calls[index] for index in sorted(calls)]
    else:
        message["tool_calls"] = []
    return message


def chat_completion(
    spec: ProviderSpec,
    key: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
    *,
    on_token: Optional[Callable[[str], None]] = None,
    timeout: float = 60.0,
    transport: Optional[httpx.BaseTransport] = None,
) -> dict:
    """One call to one provider. Raises :class:`RouterError` on any failure."""
    url = f"{spec.base_url}/chat/completions"
    headers = _headers(spec, key)
    streaming = on_token is not None

    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            if not streaming:
                response = client.post(url, json=_body(model, messages, tools, False), headers=headers)
                if response.status_code >= 400:
                    raise _http_error(response)
                payload = response.json()
                return _message_from_json(payload)

            callback = on_token
            assert callback is not None  # streaming == on_token is not None
            emitted = False
            collected: list[str] = []
            calls: dict[int, dict] = {}
            with client.stream(
                "POST", url, json=_body(model, messages, tools, True), headers=headers
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise _http_error(response)
                for line in response.iter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("error"):
                        error = event["error"]
                        code = 0
                        if isinstance(error, dict):
                            code = int(error.get("code") or 0)
                        raise RouterError(
                            f"mid-stream error: {code or 'unknown'}",
                            kind="rate_limit" if code == 429 else "stream_error",
                            retry_after=None,
                            emitted=emitted,
                        )
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        for call in delta.get("tool_calls") or []:
                            _merge_tool_call(calls, call)
                        piece = delta.get("content")
                        if piece:
                            collected.append(piece)
                            emitted = True
                            callback(piece)
            return _assemble("".join(collected), calls)
    except RouterError:
        raise
    except httpx.TimeoutException as exc:
        raise RouterError(f"timeout: {exc}", kind="timeout") from exc
    except httpx.TransportError as exc:
        raise RouterError(f"transport: {exc}", kind="transport") from exc
    except ValueError as exc:  # malformed JSON body
        raise RouterError(f"invalid response: {exc}", kind="error") from exc


def _message_from_json(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise RouterError("invalid response: not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RouterError("invalid response: no choices")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, list):  # some gateways answer with content parts
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    calls = message.get("tool_calls") or []
    return {"role": "assistant", "content": str(content), "tool_calls": list(calls)}


def discover_models(
    spec: ProviderSpec,
    key: str,
    *,
    timeout: float = 15.0,
    transport: Optional[httpx.BaseTransport] = None,
    limit: int = 40,
) -> list[ModelSpec]:
    """Models advertised by the provider, with their real tool capability.

    OpenRouter's ``GET /api/v1/models`` lists ``supported_parameters`` per entry;
    ``"tools"`` in that array is the only signal that a model can call tools.
    Absent field, absent value: the model is treated as tool-less, never assumed
    capable (the qwen2.5:0.5b lesson).
    """
    if not spec.discover:
        return []
    url = f"{spec.base_url}{spec.discover}"
    try:
        with httpx.Client(timeout=timeout, transport=transport) as client:
            response = client.get(url, headers=_headers(spec, key))
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    suffix = spec.discover_suffix
    found: list[ModelSpec] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        model_id = str(row["id"])
        if suffix and not model_id.endswith(suffix):
            continue
        params = row.get("supported_parameters")
        tools = isinstance(params, list) and "tools" in params
        found.append(ModelSpec(id=model_id, tools=tools))
    found.sort(key=lambda model: model.id)
    return found[:limit]


def pick_model(spec: ProviderSpec, requested: str, *, tools: bool) -> str:
    """The model to use for this provider: the requested one, else the first fit."""
    available = spec.tool_models if tools else spec.models
    if requested:
        ids = {model.id for model in spec.models}
        if not ids or requested in ids:
            return requested
    if available:
        return available[0].id
    if spec.models:
        return spec.models[0].id
    return requested
