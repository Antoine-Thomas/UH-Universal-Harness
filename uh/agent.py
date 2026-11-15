"""Synchronous agent loop (Jalon 1).

Contract
--------
``run_turn(messages, provider, toolsets, *, max_iterations=8) -> list[dict]``

Messages are OpenAI-style dicts (``{"role": ..., "content": ..., ...}``) and
tool calls use the Ollama ``/api/chat`` shape::

    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "read_file",
                                  "arguments": {"path": "a.txt"}}}]}

Tool results come back as ``{"role": "tool", "name": <tool>, "content": <json>}``
so the format stays portable to the providers of Jalon 2.

Streaming (Jalon 2) is opt-in: ``on_token`` is a ``Callable[[str], None]``
receiving text deltas. A provider that does not declare ``on_token`` (and has
no ``**kwargs``) is detected by signature inspection: the loop then calls it in
blocking mode and prints one warning per provider class, not one per turn.
Detection is signature-based on purpose - retrying on any ``TypeError`` would
swallow genuine bugs raised inside the provider.

Fail-closed rules:

* any exception raised by a toolset becomes a ``tool`` message holding the
  error text; the loop keeps going and nothing propagates to the caller;
* an unknown tool name becomes an error message, not a crash;
* if ``max_iterations`` provider calls happen without a final assistant
  message, ``BudgetExhausted`` is raised - the history is never silently
  truncated.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:  # the manager imports a provider module, not this one
    from .context import ContextManager


class BudgetExhausted(RuntimeError):
    """Raised when no final answer arrives within ``max_iterations`` calls."""


@runtime_checkable
class Provider(Protocol):
    """Minimal provider surface: one call, optional token streaming."""

    def chat(
        self,
        messages: List[dict],
        tools: List[dict],
        *,
        on_token: Optional[Callable[[str], None]] = None,
    ) -> dict: ...


@runtime_checkable
class Toolset(Protocol):
    """Common toolset interface (see ``uh.toolsets``)."""

    name: str
    tools: List[dict]

    def call(self, name: str, args: dict) -> dict: ...


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _as_assistant_message(response: Any) -> dict:
    """Accept either a bare assistant message or a raw provider payload."""
    if isinstance(response, dict):
        inner = response.get("message")
        if isinstance(inner, dict):
            return dict(inner)
        return dict(response)
    raise TypeError(f"provider returned {type(response).__name__}, expected dict")


def _tool_definitions(toolsets: Dict[str, "Toolset"]) -> List[dict]:
    definitions: List[dict] = []
    for toolset in toolsets.values():
        for spec in getattr(toolset, "tools", None) or []:
            if isinstance(spec, dict):
                definitions.append(spec)
    return definitions


def _tool_index(toolsets: Dict[str, "Toolset"]) -> Dict[str, "Toolset"]:
    index: Dict[str, "Toolset"] = {}
    for toolset in toolsets.values():
        for spec in getattr(toolset, "tools", None) or []:
            function = spec.get("function") if isinstance(spec, dict) else None
            if isinstance(function, dict) and function.get("name"):
                index[str(function["name"])] = toolset
    return index


def _coerce_arguments(raw: Any) -> dict:
    """Ollama sends a dict, some providers send a JSON string: accept both."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _split_call(call: Any) -> tuple[str, dict]:
    if not isinstance(call, dict):
        return "", {}
    function = call.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or ""), _coerce_arguments(function.get("arguments"))
    return str(call.get("name") or ""), _coerce_arguments(call.get("arguments"))


#: A fenced block in a language the harness can actually run. Wider than the
#: common four on purpose: a model that answers in PowerShell is in exactly the
#: same failure mode, it just names the fence differently.
_CODE_FENCE = re.compile(
    r"```(?:python|py|bash|sh|zsh|shell|js|javascript|node|powershell|ps1|cmd)\b",
    re.IGNORECASE,
)
#: Tool names that mean "this harness can run something for you". The check runs
#: on the indexed names, because that is what the model is offered - a toolset
#: named `positron` only counts if it publishes a callable such as `execute_code`.
_RUNNABLE_HINTS = ("execute", "terminal", "shell", "command", "run", "python", "code")
#: Injected once, in the language the system prompt is written in. Naming the
#: tool matters: "call a tool" is advice, "call `terminal`" is an instruction.
_NUDGE = (
    "You answered with a code block instead of using a tool. You can run this "
    "yourself: call `{tool}` now with the code as its argument. Never hand code "
    "to the user to copy and paste when a tool can do the work."
)


def _runnable_tools(index: Dict[str, "Toolset"]) -> List[str]:
    """Names of the tools that execute something, deterministically ordered.

    Provider-agnostic by construction: nothing here looks at which model is
    answering, only at what the harness can do for it.
    """
    names = [
        name
        for name in index
        if any(hint in name.lower() for hint in _RUNNABLE_HINTS)
        or any(hint in str(getattr(index[name], "name", "")).lower() for hint in _RUNNABLE_HINTS)
    ]
    # `terminal`/`execute_code` first: they run a snippet, which is what the
    # model was trying to hand over in a fenced block.
    return sorted(names, key=lambda name: (0 if "execute" in name.lower() else 1, name))


def _answers_with_code(content: Any) -> bool:
    return bool(_CODE_FENCE.search(str(content or "")))


def _execute(index: Dict[str, "Toolset"], name: str, args: dict) -> dict:
    """Run one tool, always returning a ``role: tool`` message (never raising)."""
    toolset = index.get(name)
    if toolset is None:
        payload: dict = {"error": f"unknown tool '{name}'. Known tools: {sorted(index)}"}
    else:
        try:
            result = toolset.call(name, args)
        except Exception as exc:  # noqa: BLE001 - fail-closed by contract
            payload = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            payload = result if isinstance(result, dict) else {"result": result}

    try:
        content = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        content = json.dumps({"error": f"tool result not serialisable: {exc}"})
    return {"role": "tool", "name": name, "content": content}


def last_assistant_message(messages: Iterable[dict]) -> dict | None:
    """Return the last assistant message, or ``None`` if there is none."""
    found: dict | None = None
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "assistant":
            found = message
    return found


# --------------------------------------------------------------------------- #
# streaming support detection
# --------------------------------------------------------------------------- #
# One warning per provider class, not per turn (Jalon 2 contract).
_WARNED_NO_STREAMING: set[str] = set()


def _provider_key(provider: Any) -> str:
    cls = type(provider)
    return f"{cls.__module__}.{cls.__qualname__}"


def provider_supports_streaming(provider: Any) -> bool:
    """True if ``provider.chat`` accepts ``on_token`` (explicitly or via **kwargs)."""
    try:
        parameters = inspect.signature(provider.chat).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return True
    if "on_token" in parameters:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values())


def _chat(
    provider: "Provider",
    messages: List[dict],
    tools: List[dict],
    on_token: Optional[Callable[[str], None]],
) -> dict:
    """Call the provider, degrading to blocking mode when streaming is unsupported."""
    if on_token is None:
        return provider.chat(messages, tools)
    if provider_supports_streaming(provider):
        return provider.chat(messages, tools, on_token=on_token)

    key = _provider_key(provider)
    if key not in _WARNED_NO_STREAMING:
        _WARNED_NO_STREAMING.add(key)
        print(
            f"uh: provider {key} does not support streaming; falling back to blocking mode",
            file=sys.stderr,
        )
    return provider.chat(messages, tools)


# --------------------------------------------------------------------------- #
# loop
# --------------------------------------------------------------------------- #
def run_turn(
    messages: List[dict],
    provider: "Provider",
    toolsets: Dict[str, "Toolset"],
    *,
    max_iterations: int = 8,
    on_token: Optional[Callable[[str], None]] = None,
    context: Optional["ContextManager"] = None,
    on_notice: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """Run the agent loop until a final answer or until the budget is spent.

    Returns a NEW list containing the original messages plus every assistant
    and tool message produced here. The caller's list is not mutated.

    ``on_token`` (Jalon 2) streams text deltas to the caller while the answer is
    generated; the complete message is still appended to the returned history.

    ``context`` prunes what is *sent* on every iteration, without touching the
    history this function returns: a tool loop that grows past the model's window
    is exactly when it matters, and the store keeps everything.

    ``on_notice`` receives one line when the turn ends badly but legitimately
    (a model that keeps answering with code): the caller decides whether that is
    a console line, a web event, or nothing at all.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    history: List[dict] = [dict(message) if isinstance(message, dict) else message for message in messages]
    index = _tool_index(toolsets)
    definitions = _tool_definitions(toolsets)
    total_iterations = int(max_iterations)
    runnable = _runnable_tools(index)
    # One nudge per turn, not one per iteration: a model that ignores it twice
    # will not be talked into it on the fourth try, and the user is paying for
    # every extra call.
    nudges_left = 1 if runnable else 0

    for _iteration in range(1, total_iterations + 1):  # the count is only for the error
        outgoing = context.prepare(history) if context is not None else history
        response = _chat(provider, outgoing, definitions, on_token)
        message = _as_assistant_message(response)
        message.setdefault("role", "assistant")
        if message.get("content") is None:
            message["content"] = ""
        history.append(message)

        calls = message.get("tool_calls") or []
        if not calls:
            # A turn that was asked to *do* something and came back with a fenced
            # block is not finished: the user would have to copy the code back
            # into the agent. This is the loop's job, not the prompt's, so it
            # holds for every provider instead of the ones we remembered to test.
            if nudges_left and _answers_with_code(message.get("content")):
                nudges_left -= 1
                history.append({"role": "system", "content": _NUDGE.format(tool=runnable[0])})
                continue
            if runnable and _answers_with_code(message.get("content")) and on_notice is not None:
                on_notice(
                    f"the model answered with code instead of calling `{runnable[0]}` "
                    "after a retry; the snippet was left in the transcript"
                )
            return history

        for call in calls:
            name, args = _split_call(call)
            history.append(_execute(index, name, args))

    raise BudgetExhausted(
        f"no final answer after {total_iterations} provider call(s) "
        f"(max_iterations={total_iterations}); tools: {sorted(index)}"
    )
