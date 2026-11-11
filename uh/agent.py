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

Fail-closed rules:

* any exception raised by a toolset becomes a ``tool`` message holding the
  error text; the loop keeps going and nothing propagates to the caller;
* an unknown tool name becomes an error message, not a crash;
* if ``max_iterations`` provider calls happen without a final assistant
  message, ``BudgetExhausted`` is raised - the history is never silently
  truncated.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Protocol, runtime_checkable


class BudgetExhausted(RuntimeError):
    """Raised when no final answer arrives within ``max_iterations`` calls."""


@runtime_checkable
class Provider(Protocol):
    """Minimal provider surface: one blocking call, no streaming at Jalon 1."""

    def chat(self, messages: List[dict], tools: List[dict]) -> dict: ...


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
# loop
# --------------------------------------------------------------------------- #
def run_turn(
    messages: List[dict],
    provider: "Provider",
    toolsets: Dict[str, "Toolset"],
    *,
    max_iterations: int = 8,
) -> List[dict]:
    """Run the agent loop until a final answer or until the budget is spent.

    Returns a NEW list containing the original messages plus every assistant
    and tool message produced here. The caller's list is not mutated.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    history: List[dict] = [dict(message) if isinstance(message, dict) else message for message in messages]
    index = _tool_index(toolsets)
    definitions = _tool_definitions(toolsets)
    total_iterations = int(max_iterations)

    for iteration in range(1, total_iterations + 1):
        response = provider.chat(history, definitions)
        message = _as_assistant_message(response)
        message.setdefault("role", "assistant")
        if message.get("content") is None:
            message["content"] = ""
        history.append(message)

        calls = message.get("tool_calls") or []
        if not calls:
            return history

        for call in calls:
            name, args = _split_call(call)
            history.append(_execute(index, name, args))

    raise BudgetExhausted(
        f"no final answer after {total_iterations} provider call(s) "
        f"(max_iterations={total_iterations}); tools: {sorted(index)}"
    )
