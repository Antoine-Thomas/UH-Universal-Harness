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

import json
import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Callable, Dict, List, Optional, TypeVar


class ProviderError(RuntimeError):
    """Any provider failure: network, HTTP status, malformed answer, missing model.

    Deliberately distinct from :class:`uh.agent.BudgetExhausted`: callers must be
    able to tell "the model is unreachable" from "the model kept calling tools".

    ``status`` is the HTTP status when the failure was an answer (429, 401, …):
    the fallback log line reports ``reason`` - a classified, one-line reason -
    instead of the raw message, which can quote the request body.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        hint: str = "",
        kind: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        # One clean clause, safe to print: never a payload excerpt. It rides on
        # the classified ``reason`` line so a fallback explains itself without
        # quoting the request.
        self.hint = hint
        #: ``transport`` | ``timeout`` | ``""``. Set by the provider that saw the
        #: failure: the chain needs it to tell "the network blinked" (a retry, or
        #: another provider, may work) from "this request is wrong" (nothing will).
        self.kind = kind

    @property
    def retryable(self) -> bool:
        """The SAME request may succeed a second later."""
        if self.kind in ("transport", "timeout"):
            return True
        if self.status is None:
            return False
        return self.status == 429 or self.status >= 500

    @property
    def auth_error(self) -> bool:
        """Credentials or billing: the account itself cannot answer this request.

        Measured reason to stop there rather than fall back: a 401 on the first
        provider used to be answered silently by the second, so a rotated key
        looked like "the local model is dumb today" and the bill moved without a
        word. The other 4xx are NOT in this set on purpose:
        ``400 model does not support tools`` or ``404 unknown model`` describe
        that provider, not the request - the chain is there to answer anyway
        (local model without tool support -> cloud), which is the product.
        """
        return self.status in (401, 402, 403)

    @property
    def reason(self) -> str:
        """Short classified reason for fallback lines: ``429 rate limit``."""
        if self.status is not None:
            from ..router.client import classify_status

            base = f"{self.status} {classify_status(self.status).replace('_', ' ')}"
            return f"{base}: {self.hint}" if self.hint else base
        if self.kind in ("transport", "timeout"):
            return self.kind
        return type(self).__name__


#: One provider call is retried this many times on a transport failure, and no
#: more: three attempts cost at most 1.5 s, and a fourth delays the fallback the
#: user is waiting for.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = 0.5

T = TypeVar("T")


def retry_transport(
    send: Callable[[], T],
    *,
    label: str,
    emitted: Optional[Callable[[], bool]] = None,
    attempts: int = RETRY_ATTEMPTS,
    backoff: float = RETRY_BACKOFF_S,
    sleep: Optional[Callable[[float], None]] = None,
    on_retry: Optional[Callable[[int, ProviderError], None]] = None,
) -> T:
    """Run ``send`` again when the failure was the network, never when it was the request.

    ``emitted`` answers "has a token already reached the caller?". A stream that
    died after the first words must NOT be replayed: the user would read the same
    sentence twice. That case belongs to the turn (partial answer + "Continuer"),
    not to a silent retry.
    """
    wait = sleep or time.sleep
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            return send()
        except ProviderError as exc:
            if not exc.retryable:
                raise
            if emitted is not None and emitted():
                raise
            if attempt == total:
                raise
            if on_retry is not None:
                try:
                    on_retry(attempt, exc)
                except Exception:  # noqa: BLE001 - a trace never breaks a retry
                    pass
            wait(backoff * (2 ** (attempt - 1)))
    raise AssertionError("unreachable: the loop returns or raises")



def request_messages(
    messages: List[dict], wire: str = "openai", tools: Optional[List[dict]] = None
) -> List[dict]:
    """Copy of ``messages`` as a provider should see it - never the stored dicts.

    The messages are stored in whatever shape the provider that produced them
    used, and replayed to whichever provider answers next. Those shapes differ:
    Ollama returns ``{id, function: {index, name, arguments: <object>}}``, OpenAI
    and DeepSeek demand ``{id, type: "function", function: {name, arguments:
    "<json string>"}}``. Measured on 2026-11-18, switching model inside one
    session sent the first shape to DeepSeek (``422 missing field 'type'``) and
    the second to Ollama (``400 Value looks like object, but can't find closing
    '}'``): the whole conversation became unusable from one bad message onwards.

    So this is the boundary, and it decides once for every provider:

    * ``wire="openai"``: every tool call gets an ``id``, ``type: "function"``, and
      ``arguments`` as a JSON **string**; ``index`` is dropped (it belongs to
      streaming deltas, not to a replayed message). An argument string that is
      not valid JSON is *repaired*, then replaced by ``{}`` - never sent as is,
      because the provider rejects the entire request over one bad call.
    * ``wire="ollama"``: the same call is rebuilt with ``arguments`` as an
      **object**, and without ``type``/``index``.
    * an empty ``tool_calls`` list is removed. OpenAI tolerates
      ``"tool_calls": []`` on an assistant message; DeepSeek does not, and
      rejects the *next* turn with ``400 Invalid 'messages[N].tool_calls': empty
      array`` because the assistant message is replayed from history.
    * a ``tool`` message without ``tool_call_id`` inherits the id of the call it
      answers: without it OpenAI refuses the request, and the model cannot pair
      result and call.
    * when the caller passes the ``tools`` this request declares, a replayed call
      to a tool it does not offer is **dropped**. A strict gateway rejects the
      whole request over one such call - OmniRouter answered ``attempted to call
      tool 'mcp__positron__execute_code' which was not in request.tools`` on
      19/11, and the session could not continue. Without a tool list nothing is
      filtered, so every existing caller keeps its behaviour.
    * the **image bytes are dropped from a tool result**. The interface needs
      them (`artifacts[].data_url`, kept in the session so a reload redraws the
      chart); a text model does not, and it already has the marker
      ``[image: image/png, N base64 characters, artefact 1]``. Measured cost of
      forgetting this: one 40 KB chart is ~10 000 tokens of noise on every
      following turn, paid to the API for a picture nothing can read.
    """
    cleaned: List[dict] = []
    last_calls: List[str] = []
    declared = _declared_tool_names(tools)
    for message in messages:
        if not isinstance(message, dict):
            cleaned.append(message)
            continue
        if message.get("role") == "assistant":
            last_calls = [call["id"] for call in _wire_tool_calls(message.get("tool_calls"), wire)]
        if "tool_calls" in message and not message["tool_calls"]:
            message = {key: value for key, value in message.items() if key != "tool_calls"}
        if message.get("artifacts"):
            # An answer may carry images (data URLs) for the interface. A provider
            # pays for them in tokens and cannot read them: the text survives, the
            # bytes stay in the session.
            message = {key: value for key, value in message.items() if key != "artifacts"}
        if message.get("role") == "tool":
            content = _without_image_bytes(message.get("content"))
            if content is not message.get("content"):
                message = {**message, "content": content}
            if not str(message.get("tool_call_id") or "").strip() and last_calls:
                message = {**message, "tool_call_id": last_calls.pop(0)}
        if message.get("tool_calls"):
            calls = _wire_tool_calls(message["tool_calls"], wire)
            if declared is not None:
                calls = [call for call in calls if _call_name(call) in declared]
            if calls:
                message = {**message, "tool_calls": calls}
            else:
                # A replayed call to a tool this request does not declare. Replaying
                # it makes a strict gateway reject the WHOLE request ("attempted to
                # call tool 'mcp__positron__execute_code' which was not in
                # request.tools", OmniRouter 19/11), and keeping an assistant
                # message with no content and no calls is refused just as well.
                message = {key: value for key, value in message.items() if key != "tool_calls"}
                if not str(message.get("content") or "").strip():
                    continue
        cleaned.append(message)
    return _close_open_calls(cleaned)


def _declared_tool_names(tools: Optional[List[dict]]) -> Optional[set]:
    """The names this request offers, or ``None`` when the caller did not say.

    ``None`` means "do not filter": every existing caller that passes no tool list
    keeps the behaviour it had, filtering is opt-in and therefore testable.
    """
    if not tools:
        return None
    names = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = (function or {}).get("name") if isinstance(function, dict) else None
        names.add(str(name or tool.get("name") or ""))
    names.discard("")
    return names or None


def _call_name(call: dict) -> str:
    function = call.get("function")
    return str((function or {}).get("name") or "") if isinstance(function, dict) else ""


def _close_open_calls(messages: List[dict]) -> List[dict]:
    """Make the sequence legal: no orphan tool_call, no orphan tool message.

    Measured 2026-11-18/19: an interrupted turn persisted an assistant message
    with ``tool_calls`` and no matching ``role: tool`` answer. The next request
    then carried an orphan call, and DeepSeek answered 422 while Ollama answered
    400 - the conversation could not continue at all.

    Two repairs, both at the boundary so the store keeps exactly what happened:

    * a call with no answer gets a synthetic tool message ("interrupted: no result
      was recorded"). The model sees that the call failed instead of a hole;
    * a tool message whose ``tool_call_id`` matches no call is dropped - strict
      APIs reject an answer nobody asked for.

    Order is preserved: each group of answers stays right after its assistant.
    """
    pending: Dict[str, str] = {}  # call id -> tool name, for the group being closed
    closed: List[dict] = []

    def flush() -> None:
        for call_id, name in list(pending.items()):
            closed.append(
                {
                    "role": "tool",
                    "name": name,
                    "tool_call_id": call_id,
                    "content": json.dumps(
                        {
                            "error": (
                                f"the call to `{name}` was interrupted before it answered: "
                                "no result was recorded"
                            ),
                            "type": "tool_error",
                            "tool": name,
                            "reason": "orphan_call",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        pending.clear()

    for message in messages:
        role = str(message.get("role") or "") if isinstance(message, dict) else ""
        if role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id and call_id in pending:
                pending.pop(call_id, None)
                closed.append(message)
            # else: an answer to a call nobody made - dropped on purpose.
            continue
        flush()
        closed.append(message)
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict) and call.get("id"):
                    raw_function = call.get("function")
                    function: Dict[str, object] = (
                        raw_function if isinstance(raw_function, dict) else {}
                    )
                    pending[str(call["id"])] = str(
                        function.get("name") or call.get("name") or "tool"
                    )
    flush()
    return closed


def _arguments_object(value: object) -> dict:
    """Tool arguments as an object: parsed when a string, ``{}`` when hopeless."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    parsed = _parse_arguments(value)
    return parsed if parsed is not None else {}


def _arguments_text(value: object) -> str:
    """Tool arguments as the JSON string OpenAI-compatible APIs demand.

    A dict is serialised. A string is validated, and repaired if it is merely
    malformed - a truncated chart snippet is a classic from a small local model.
    What cannot be repaired becomes ``{}``: an empty call the tool will reject
    with a readable error beats a whole turn refused with a 400 that names none.
    """
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    if not isinstance(value, str) or not value.strip():
        return "{}"
    if _parse_arguments(value) is not None:
        return value
    repaired = _repair_arguments(value)
    return repaired if repaired is not None else "{}"


def _parse_arguments(text: str) -> Optional[Dict[str, object]]:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _repair_arguments(text: str) -> str | None:
    """Best effort on the two defects small models produce: never raises.

    A trailing comma before a closing brace, and a truncated object (braces or
    quotes left open). Both are closed here rather than dropped; anything else
    returns ``None`` and the caller falls back to ``{}``.
    """
    candidate = text.strip()
    if not candidate.startswith("{"):
        return None
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    opens = candidate.count("{") - candidate.count("}")
    if opens > 0:
        # A truncated object almost always stops inside a string: close the quote
        # when the number of quotes is odd, then the braces it left open.
        if candidate.count('"') % 2:
            candidate += '"'
        candidate += "}" * opens
    elif opens < 0:
        return None
    parsed = _parse_arguments(candidate)
    return candidate if parsed is not None else None


def _wire_tool_calls(raw: object, wire: str) -> List[dict]:
    """One tool_call list in the shape ``wire`` expects - ids kept, indexes gone."""
    calls: List[dict] = []
    entries = raw if isinstance(raw, list) else []
    for call in entries:
        if not isinstance(call, dict):
            continue
        raw_function = call.get("function")
        function: Dict[str, object] = raw_function if isinstance(raw_function, dict) else {}
        name = str(function.get("name") or call.get("name") or "")
        arguments = function.get("arguments", call.get("arguments"))
        call_id = str(call.get("id") or f"call_{uuid.uuid4().hex[:12]}")
        if wire == "ollama":
            calls.append(
                {
                    "id": call_id,
                    "function": {"name": name, "arguments": _arguments_object(arguments)},
                }
            )
        else:
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": _arguments_text(arguments)},
                }
            )
    return calls


def _without_image_bytes(content: object) -> object:
    """A tool payload minus its ``artifacts``, or the value untouched.

    Only a JSON object that actually carries ``artifacts`` is rewritten: a plain
    text result, a malformed payload and any other shape go through unchanged,
    so nothing that used to work can start failing here.
    """
    if not isinstance(content, str) or '"artifacts"' not in content:
        return content
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(payload, dict) or "artifacts" not in payload:
        return content
    stripped = {key: value for key, value in payload.items() if key != "artifacts"}
    try:
        return json.dumps(stripped, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return content


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
        #: Transport retry, per instance so a test can set it to one attempt and
        #: a zero delay without touching the module constants.
        self.retry_attempts: int = RETRY_ATTEMPTS
        self.retry_backoff: float = RETRY_BACKOFF_S
        self.retry_sleep: Callable[[float], None] = time.sleep

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
