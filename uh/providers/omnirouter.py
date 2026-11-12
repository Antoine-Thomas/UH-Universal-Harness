"""OmniRouter provider: a local OmniRoute gateway behind the OpenAI API.

Measured facts about the installed OmniRoute (3.8.52, data dir
``~/.omniroute``) - see README, "OmniRouter discovery":

* it is **not** configured by YAML/TOML; the data dir holds a dotenv file
  (``~/.omniroute/.env``) plus an opaque database file, and the npm package
  ships another dotenv with ``PORT``/``BASE_URL``;
* the server listens on ``http://127.0.0.1:20128`` and answers
  ``GET /v1/models`` and ``POST /v1/chat/completions`` (OpenAI-compatible,
  SSE for ``stream: true``, ``Bearer`` API key).

Base URL resolution order (first hit wins, no guessing beyond the defaults):

1. ``UH_OMNIROUTER_URL``                 (explicit, highest priority)
2. ``OMNIROUTE_BASE_URL``                (the OmniRoute CLI's own variable)
3. ``BASE_URL`` in ``~/.omniroute/.env``
4. ``http://{HOST}:{PORT}`` from that dotenv (``PORT`` defaults to 20128)
5. otherwise ``ProviderError("OmniRouter not configured: set UH_OMNIROUTER_URL")``

API key: ``UH_OMNIROUTER_API_KEY``, then ``OMNIROUTE_API_KEY``, then the
dotenv file. The key is only ever sent as a header - never printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, List, Optional

import httpx

from . import Provider, ProviderError, request_messages

DEFAULT_OMNIROUTER_PORT = 20128
DEFAULT_MODEL = "auto/best-free"
DEFAULT_TIMEOUT = 120.0
DISCOVERY_TIMEOUT = 5.0
ENV_FILE = Path.home() / ".omniroute" / ".env"


# --------------------------------------------------------------------------- #
# configuration discovery
# --------------------------------------------------------------------------- #
def config_path() -> Path | None:
    """The dotenv file used for discovery, if it exists."""
    override = os.environ.get("UH_OMNIROUTER_CONFIG")
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_file() else None
    return ENV_FILE if ENV_FILE.is_file() else None


def read_dotenv(path: Path) -> dict:
    """Minimal ``KEY=VALUE`` parser - no YAML/TOML dependency needed here."""
    values: dict = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        value = raw.strip().strip('"').strip("'")
        values[key.strip().lstrip("\ufeff")] = value
    return values


def _dotenv() -> dict:
    path = config_path()
    return read_dotenv(path) if path else {}


def resolve_base_url() -> str | None:
    for env_var in ("UH_OMNIROUTER_URL", "OMNIROUTE_BASE_URL"):
        value = os.environ.get(env_var)
        if value:
            return value.rstrip("/")

    values = _dotenv()
    if values.get("BASE_URL"):
        return str(values["BASE_URL"]).rstrip("/")

    host = values.get("OMNIROUTE_SERVER_HOST") or values.get("HOST")
    if host:
        if "://" not in host:
            port = values.get("PORT") or str(DEFAULT_OMNIROUTER_PORT)
            host = f"http://{host}:{port}"
        return host.rstrip("/")
    return None


def resolve_api_key() -> str | None:
    for env_var in ("UH_OMNIROUTER_API_KEY", "OMNIROUTE_API_KEY"):
        value = os.environ.get(env_var)
        if value:
            return value
    return _dotenv().get("OMNIROUTE_API_KEY") or None


def is_configured() -> bool:
    return resolve_base_url() is not None


# --------------------------------------------------------------------------- #
# provider
# --------------------------------------------------------------------------- #
class OmniRouterProvider(Provider):
    name = "omnirouter"
    label = "OmniRouter"  # overridden by subclasses (e.g. a user's own gateway)
    # The OpenAI path appended to ``base_url``. Subclasses whose vendor does not
    # use the ``/v1`` prefix (DeepSeek) override it.
    chat_path = "/v1/chat/completions"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        resolved = (base_url or resolve_base_url() or "").rstrip("/")
        if not resolved:
            raise ProviderError(
                "OmniRouter not configured: set UH_OMNIROUTER_URL "
                "(e.g. http://127.0.0.1:20128)"
            )
        self.base_url = resolved
        self.model = model or DEFAULT_MODEL
        self.api_key = api_key or resolve_api_key()
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------- #
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _error(self, exc: Exception) -> ProviderError:
        return ProviderError(
            f"cannot reach {self.label} at {self.base_url} ({type(exc).__name__}: {exc})"
        )

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

        if on_token is None:
            return self._chat_blocking(payload)
        return self._chat_streaming(payload, on_token)

    def _chat_blocking(self, payload: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}{self.chat_path}",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc

        data = self._payload(response)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ProviderError(f"{self.label} response has no 'choices' entry")
        message = choices[0].get("message") or {}
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": _normalise_tool_calls(message.get("tool_calls")),
        }

    def _chat_streaming(self, payload: dict, on_token: Callable[[str], None]) -> dict:
        parts: List[str] = []
        fragments: dict = {}
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}{self.chat_path}",
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            ) as response:
                if response.status_code != 200:
                    body = "".join(response.iter_text())[:300]
                    raise ProviderError(
                        f"{self.label} HTTP {response.status_code}: {body}",
                        status=response.status_code,
                    )
                for line in response.iter_lines():
                    text = (line or "").strip()
                    if not text or not text.startswith("data:"):
                        continue
                    chunk = text[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        detail = event["error"]
                        if isinstance(detail, dict):
                            detail = detail.get("message") or detail
                        raise ProviderError(f"OmniRouter error: {detail}")
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content:
                            parts.append(content)
                            on_token(content)
                        _merge_tool_call_fragments(fragments, delta.get("tool_calls"))
        except httpx.HTTPError as exc:
            raise self._error(exc) from exc

        return {
            "role": "assistant",
            "content": "".join(parts),
            "tool_calls": [fragments[index] for index in sorted(fragments)],
        }

    def _payload(self, response: httpx.Response) -> dict:
        if response.status_code == 401:
            raise ProviderError(
                f"{self.label} rejected the API key (HTTP 401): "
                "check `uh providers list` and re-add it",
                status=401,
            )
        if response.status_code != 200:
            raise ProviderError(
                f"{self.label} HTTP {response.status_code}: {(response.text or '')[:300]}",
                status=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.label} returned non-JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ProviderError(f"{self.label} returned an unexpected payload")
        if data.get("error"):
            detail = data["error"]
            if isinstance(detail, dict):
                detail = detail.get("message") or detail
            raise ProviderError(f"{self.label} error: {detail}")
        return data


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _normalise_tool_calls(raw) -> List[dict]:
    calls: List[dict] = []
    for call in raw or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        calls.append({"function": {"name": function.get("name", ""), "arguments": arguments or {}}})
    return calls


def _merge_tool_call_fragments(fragments: dict, raw) -> None:
    """OpenAI streams tool calls as deltas keyed by ``index``; merge the strings."""
    for position, call in enumerate(raw or []):
        if not isinstance(call, dict):
            continue
        index = call.get("index", position)
        slot = fragments.setdefault(index, {"function": {"name": "", "arguments": ""}})
        function = call.get("function") or {}
        if function.get("name"):
            slot["function"]["name"] += str(function["name"])
        if function.get("arguments"):
            slot["function"]["arguments"] += str(function["arguments"])


def list_remote_models(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = DISCOVERY_TIMEOUT,
) -> List[str]:
    """Model ids exposed by OmniRoute, for ``uh providers list``."""
    root = (base_url or resolve_base_url() or "").rstrip("/")
    if not root:
        raise ProviderError("OmniRouter not configured: set UH_OMNIROUTER_URL")
    headers = {}
    key = api_key or resolve_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        response = httpx.get(f"{root}/v1/models", headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ProviderError(f"cannot reach OmniRouter at {root} ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise ProviderError(f"OmniRouter HTTP {response.status_code} on /v1/models")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError(f"OmniRouter returned non-JSON body on /v1/models: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ProviderError("unexpected /v1/models payload: missing 'data' list")
    return [str(entry.get("id")) for entry in data if isinstance(entry, dict) and entry.get("id")]
