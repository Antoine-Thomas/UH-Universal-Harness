"""Synchronous agent loop (Jalon 1).

Contract
--------
``run_turn(messages, provider, toolsets, *, max_iterations=8) -> list[dict]``

Messages are OpenAI-style dicts (``{"role": ..., "content": ..., ...}``) and
tool calls use the Ollama ``/api/chat`` shape::

    {"role": "assistant", "content": "",
     "tool_calls": [{"function": {"name": "read_file",
                                  "arguments": {"path": "a.txt"}}}]}

Tool results come back as ``{"role": "tool", "name": <tool>, "content": <json>,
"tool_call_id": <id>}``: the id is what ties an answer to the call that asked for
it, and without it two parallel calls are indistinguishable - the second result
lands where the first was expected. Providers that omit ids get them assigned
here, so the pair in the history is always consistent.

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
import uuid
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


def _ensure_call_ids(message: dict) -> None:
    """Give every tool call an id, in place, so its answer can name it.

    Ollama's shape has no ``id``; OpenAI's requires one on both sides. Assigning
    it once here - before the calls are executed - keeps the pair consistent even
    for a provider that sends neither, instead of storing an answer that cannot
    be matched to its question when the session is replayed.
    """
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return
    for call in calls:
        if not isinstance(call, dict):
            continue
        if not call.get("id"):
            call["id"] = f"call_{uuid.uuid4().hex[:12]}"
        if not call.get("type"):
            # OpenAI/DeepSeek l'exigent a la relecture ; Ollama ne l'envoie pas.
            call["type"] = "function"


#: How many times a model may ask for the same forbidden tool before the turn is
#: stopped. One refusal is information; two is a retry; three is a loop that costs
#: the user a provider call each time and cannot succeed.
_REFUSAL_LIMIT = 3

#: How many times a turn re-asks a model that answered with a code block instead of
#: calling a tool it was offered (point 3). Two, not one: a small local model often
#: needs the format repeated before it acts, and a third attempt has never been
#: observed to change the outcome - it only multiplies the bill.
_NUDGE_LIMIT = 2


def _split_call(call: Any) -> tuple[str, str, dict]:
    """``(call_id, name, arguments)`` for either tool-call shape a provider sends."""
    if not isinstance(call, dict):
        return "", "", {}
    call_id = str(call.get("id") or "")
    function = call.get("function")
    if isinstance(function, dict):
        return (
            call_id,
            str(function.get("name") or ""),
            _coerce_arguments(function.get("arguments")),
        )
    return call_id, str(call.get("name") or ""), _coerce_arguments(call.get("arguments"))


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
#: Injected when a model answers with a code block instead of calling a tool it was
#: offered. Naming the tool matters: "call a tool" is advice, "call `execute_code`"
#: is an instruction - and the last sentence closes the door on handing code back
#: a third time, which is what a small model does when it is only told "you can".
_NUDGE = (
    "You answered with a code block instead of calling the tool, and the code was "
    "not executed: the user has to copy it, which is not the job. Call `{tool}` now, "
    "with that code as its argument, and let the tool run it. Do not paste the code "
    "again, do not describe what it would print: call `{tool}`."
)
#: A stable substring of `_NUDGE`, so a caller can recognise the retry instruction
#: in a returned history without depending on the whole sentence. Used to keep it
#: out of the stored transcript (it is protocol, not conversation) and to count it.
NUDGE_MARK = "instead of calling the tool"
#: A request that *asks for the code* is not a missed tool call: nudging it would
#: fight the operator's own instruction, twice, at their expense. Deliberately
#: narrow - a handful of explicit phrasings, never a semantic guess: a wrong
#: suppression would silently disable point 3 for the questions it exists for.
_CODE_REQUEST = re.compile(
    r"(montre[- ]moi le code|donne[- ]moi le code|[ée]cris[- ]moi le code"
    r"|sans l'ex[ée]cuter|write the code|show me the code|pseudo[- ]?code"
    r"|exemple de code|code source)",
    re.IGNORECASE,
)
#: The provider stopped because max_tokens ran out. The answer is real but
#: unfinished, and a transcript that does not say so reads as a broken model.
TRUNCATED_NOTICE = (
    "réponse tronquée par « Tokens max » avant la fin : relance, ou monte "
    "« Tokens max » dans le panneau 🎚 de la conversation"
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


def _asks_for_code(messages: List[dict]) -> bool:
    """True when the operator's own request asks for the code, not for a result.

    Read from the last user message only: an older turn asking for a snippet must
    not silence the nudge for the current one.
    """
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return bool(_CODE_REQUEST.search(str(message.get("content") or "")))
    return False


def _tool_parameters(toolset: "Toolset", name: str) -> Dict[str, Any]:
    """The JSON Schema the model was shown for this tool, or ``{}``."""
    for spec in getattr(toolset, "tools", None) or []:
        function = spec.get("function") if isinstance(spec, dict) else None
        if isinstance(function, dict) and function.get("name") == name:
            parameters = function.get("parameters")
            return parameters if isinstance(parameters, dict) else {}
    return {}


def _argument_problem(toolset: "Toolset", name: str, args: dict) -> Optional[str]:
    """Why these arguments cannot run, or ``None`` when they can.

    The signature comes from the tool's own schema - the catalogue the model was
    given - so one check covers the file, terminal, uhb and MCP tools alike. It
    is what turns "the call was refused" into a sentence the model can act on:
    the two failures are different, and only one of them is about permissions.

    This is also the guard for repaired arguments: a call whose JSON had to be
    reduced to ``{}`` cannot run a tool that declares required parameters.
    """
    parameters = _tool_parameters(toolset, name)
    properties = parameters.get("properties")
    allowed = set(properties) if isinstance(properties, dict) else set()
    required = [key for key in (parameters.get("required") or []) if isinstance(key, str)]
    if allowed:
        unknown = sorted(key for key in args if key not in allowed)
        if unknown:
            return (
                f"unknown argument(s) {', '.join(unknown)} for `{name}`; "
                f"expected: {', '.join(sorted(allowed))}"
            )
    missing = [key for key in required if args.get(key) in (None, "")]
    if missing:
        return (
            f"missing required argument(s) {', '.join(missing)} for `{name}`; "
            f"expected: {', '.join(sorted(required))}"
        )
    return None


def _missing_explanation(toolsets: Optional[Dict[str, "Toolset"]], name: str) -> Optional[dict]:
    """Ask the toolsets whether the *mode* is why this tool is missing.

    A toolset that knows its mode answers with a typed refusal naming it; the
    others have nothing to say. One loopback question on an error path, never on
    a normal call — and it grants nothing: an explanation is not a permission.
    """
    for toolset in (toolsets or {}).values():
        explain = getattr(toolset, "explain_missing", None)
        if not callable(explain):
            continue
        try:
            answer = explain(name)
        except Exception:  # noqa: BLE001 - an explanation never breaks a turn
            continue
        if isinstance(answer, dict) and answer:
            return answer
    return None


def _execute(
    index: Dict[str, "Toolset"],
    name: str,
    args: dict,
    call_id: str = "",
    toolsets: Optional[Dict[str, "Toolset"]] = None,
) -> tuple[dict, bool]:
    """Run one tool, always returning a ``role: tool`` message and a refusal flag.

    The flag is what lets the loop stop a model that keeps asking for a tool it is
    not allowed to use: without it, three refusals cost three provider calls and
    the user pays for a conversation that cannot progress.

    A refusal is structured, not a sentence: ``type: "forbidden"`` for a tool that
    is not offered in this mode, ``type: "invalid_arguments"`` for a call the
    tool's own schema rejects (nothing is executed), ``type: "tool_error"`` for a
    tool that raised. The interface shows them all, and none is retried silently.
    """
    toolset = index.get(name)
    refused = False
    if toolset is None:
        refused = True
        payload: dict = {
            "error": f"tool '{name}' is not available in this mode. Available: {sorted(index)}",
            "type": "forbidden",
            "tool": name,
            "reason": "unknown_tool",
        }
        # The mode is the cause more often than the name is wrong: a model replays
        # a tool it saw in a previous mode. Ask the toolset that knows, so the
        # refusal names the mode and the interface can offer « Autoriser » (point
        # 2) instead of an anonymous "unknown tool".
        explanation = _missing_explanation(toolsets, name)
        if explanation is not None:
            payload.update(explanation)
        elif name == "execute_code":
            # The one absence that has a precise, actionable cause. "Not allowed
            # in this mode" sent the operator looking at modes and personas for
            # an hour: the tool is published only when the process that serves
            # the chat was started with the switch, and no config carries it.
            payload["error"] = (
                "`execute_code` is not published by this server: it only appears when the "
                "process that serves the chat was started with UH_UHB_EXECUTE=1 "
                "(a process variable, never a config file), and UHB must answer on its "
                "loopback URL. Restart the server with that variable to get the tool."
            )
            payload["reason"] = "execute_code_not_exposed"
    else:
        problem = _argument_problem(toolset, name, args)
        if problem is not None:
            refused = True
            payload = {
                "error": problem,
                "type": "invalid_arguments",
                "tool": name,
                "reason": "invalid_arguments",
                "expected": sorted((_tool_parameters(toolset, name).get("properties") or {})),
            }
        else:
            try:
                result = toolset.call(name, args)
            except Exception as exc:  # noqa: BLE001 - fail-closed by contract
                payload = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "type": "tool_error",
                    "tool": name,
                }
            else:
                payload = result if isinstance(result, dict) else {"result": result}

    try:
        content = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        content = json.dumps({"error": f"tool result not serialisable: {exc}"})
    message: dict = {"role": "tool", "name": name, "content": content}
    if call_id:
        message["tool_call_id"] = call_id
    return message, refused


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


def _images_payload(paths: Optional[List[str]], limit: int = 6) -> List[str]:
    """Base64 of the prepared copies, bounded - or nothing at all.

    Only the ``-sent`` copies are read (already under 1 MB, see ``uh/media``), never
    the original: a 4K screenshot must not become a 8 MB request. A path that no
    longer exists is skipped rather than failing the turn: a purge between the click
    and the send must not cost the operator their question.
    """
    import base64
    from pathlib import Path

    encoded: List[str] = []
    for raw in (paths or [])[:limit]:
        try:
            data = Path(raw).read_bytes()
        except OSError:
            continue
        if not data or len(data) > 2_000_000:
            continue
        encoded.append(base64.b64encode(data).decode("ascii"))
    return encoded


def _with_images(messages: List[dict], paths: Optional[List[str]]) -> List[dict]:
    """The list sent to a provider, with the images attached to the last user turn.

    A copy on purpose: ``_images`` must never reach the store - a base64 blob in the
    SQLite thread is exactly what P1-14 forbids. The stored history keeps the text,
    which already names each attachment.
    """
    payload = _images_payload(paths)
    if not payload:
        return messages
    copy = [dict(message) for message in messages]
    for message in reversed(copy):
        if message.get("role") == "user":
            message["_images"] = payload
            break
    return copy


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
    images: Optional[List[str]] = None,
    offer_tools: bool = True,
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

    from .providers import take_drops

    history: List[dict] = [
        dict(message) if isinstance(message, dict) else message for message in messages
    ]
    index = _tool_index(toolsets)
    definitions = _tool_definitions(toolsets)
    if not offer_tools:
        # A model that cannot call tools (measured, not guessed) must not be sent a
        # tool list: Ollama answers 400 "does not support tool calling" and the whole
        # turn dies over a capability the model never had. The mode gate has already
        # decided what was allowed; this only stops us asking for something the model
        # cannot do.
        index = {}
        definitions = []
    total_iterations = int(max_iterations)
    runnable = _runnable_tools(index)
    #: How many times this turn may re-ask for a tool call (point 3). Two attempts,
    #: only when a tool that runs something was actually offered: in a read-only
    #: mode `runnable` is empty, and a code answer is the *correct* answer there -
    #: the snippet is the deliverable, not a missed call. Same when the operator
    #: explicitly asked for the code: the nudge would fight their own sentence.
    wants_code = _asks_for_code(history)
    nudges_left = 0 if wants_code else (_NUDGE_LIMIT if runnable else 0)
    #: Per (tool, arguments): how many more refusals the loop tolerates this turn.
    refusals_left: Dict[tuple[str, str], int] = {}

    for _iteration in range(1, total_iterations + 1):  # the count is only for the error
        outgoing = context.prepare(history) if context is not None else history
        response = _chat(provider, _with_images(outgoing, images), definitions, on_token)
        # The boundary may have removed a call to a tool this request does not
        # declare (a strict gateway refuses the whole request otherwise). That is a
        # fact about the conversation, so it is said once, plainly, instead of
        # disappearing into a log nobody reads.
        if on_notice is not None:
            for dropped in take_drops():
                on_notice(
                    f"appel d'outil retiré de l'historique : `{dropped['tool']}` "
                    f"({dropped['reason']}) — l'outil n'est plus proposé dans cette requête"
                )
        # ``truncated`` is a fact about the call, not part of the message: it is
        # popped here so the history - and the next provider payload - stays the
        # three keys every backend accepts.
        truncated = bool(response.get("truncated")) if isinstance(response, dict) else False
        if isinstance(response, dict):
            response.pop("truncated", None)
            inner_message = response.get("message")
            if isinstance(inner_message, dict):
                inner_message.pop("truncated", None)
        message = _as_assistant_message(response)
        message.setdefault("role", "assistant")
        if message.get("content") is None:
            message["content"] = ""
        _ensure_call_ids(message)
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
            if (
                runnable
                and not wants_code
                and _answers_with_code(message.get("content"))
                and on_notice is not None
            ):
                on_notice(
                    f"le modèle a répondu par du code au lieu d'appeler `{runnable[0]}` "
                    f"après {_NUDGE_LIMIT} relances : le code reste dans le transcript, "
                    "il n'a pas été exécuté"
                )
            if truncated and on_notice is not None:
                on_notice(TRUNCATED_NOTICE)
            return history

        for call in calls:
            call_id, name, args = _split_call(call)
            result, refused = _execute(index, name, args, call_id, toolsets)
            history.append(result)
            if not refused:
                continue
            # One counter per name AND arguments, not per name: a model repeating
            # the same invalid call is stuck, while the same tool asked with other
            # arguments may still succeed on the next iteration.
            signature = (name, json.dumps(args, sort_keys=True, default=str))
            refusals_left[signature] = refusals_left.get(signature, _REFUSAL_LIMIT) - 1
            # The reason lives in the tool message's payload, not in the message.
            reason = "forbidden"
            detail = ""
            try:
                decoded = json.loads(result.get("content") or "{}")
                reason = str(decoded.get("reason") or reason)
                detail = str(decoded.get("error") or "")
            except (TypeError, ValueError):
                pass
            if on_notice is not None:
                on_notice(
                    f"`{name}` refusé ({reason}) : {detail} "
                    f"(il en reste {max(refusals_left[signature], 0)})"
                )
            if refusals_left[signature] <= 0:
                # The model keeps asking for the same thing. Continuing would burn
                # the iteration budget on a conversation that cannot progress, so
                # the turn stops here with the refusal in the transcript - visible,
                # persisted, and honest about the reason.
                if on_notice is not None:
                    on_notice(
                        f"tour arrêté : le même appel à `{name}` a été refusé "
                        f"{_REFUSAL_LIMIT} fois de suite ({reason}) ; "
                        "il faut un autre argument ou un autre outil pour avancer"
                    )
                return history

    raise BudgetExhausted(
        f"no final answer after {total_iterations} provider call(s) "
        f"(max_iterations={total_iterations}); tools: {sorted(index)}"
    )
