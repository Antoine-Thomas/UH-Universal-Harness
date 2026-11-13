"""Context pruning: keep a long conversation inside a small window.

A 4k model cannot hold a hundred messages, and UH keeps every message in SQLite
forever. So the *payload* is pruned, never the history: the store keeps all of
it, the provider gets what fits.

The shape, when the budget is exceeded:

    [system prompt]            always kept - it is the rules
    [first user message]       always kept - it is the question
    [first assistant message]  kept when there is one: continuity of voice
    [summary of the middle]    one system line, model-written or condensed
    [last N messages]          kept intact - it is what the turn is about

Three levers, applied in order and each re-measured:

1. tool results over :data:`TOOL_LIMIT` bytes are truncated with a marker;
2. the middle is replaced by a summary;
3. the tail shrinks (5 -> 3 -> 1) and the summary is cut, until it fits.

The summary is written by a model only when a ``summarizer`` is given: pruning
must not spend a token just because the caller asked for a shorter payload. With
no summarizer the fallback is deterministic - role and first line of each dropped
message - which is worse prose and better than silence.

Budget resolution, first match wins:

* ``UH_CONTEXT_BUDGET`` (tokens);
* config ``context_budget_tokens``;
* 75% of the model's native window, read from Ollama's ``POST /api/show``;
* :data:`DEFAULT_BUDGET`.

Token estimation uses ``tiktoken`` when it is installed (``cl100k_base``, which
is the wrong tokenizer for a Llama or Qwen model but the right order of
magnitude), and falls back to four characters per token.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

#: Kept intact at the end of the payload: the messages the turn is about.
KEEP_TAIL = 5
#: A tool result longer than this is truncated, whatever the budget.
TOOL_LIMIT = 2048
#: Used when nothing else can be known about the model.
DEFAULT_BUDGET = 3000
#: Fraction of the native window we are willing to fill.
NATIVE_RATIO = 0.75
#: Tokens are roughly this many characters when tiktoken is absent.
CHARS_PER_TOKEN = 4
#: The marker a truncated tool result carries, so the model knows it was cut.
TRUNCATION = "…[{omitted} characters omitted]"

#: A summary is reused until the middle grows past one of these. Re-summarising
#: every turn spends a model call to cover one more exchange - measured at 8
#: summaries for 15 turns. Growth is counted in **exchanges** (user messages), the
#: unit a person means by "4 messages": one exchange is two rows in the payload,
#: so four rows would re-summarise every second turn and change nothing.
#:
#: The character arm is 4 x 2 KB rather than 2 KB for the same reason: with the
#: 500-character messages used in the measurement, a 2 KB arm fires every 1.3
#: turns and simply takes over from the exchange arm.
SUMMARY_MIN_TURNS = 4
SUMMARY_MIN_CHARS = 8192

Summarizer = Callable[[List[dict]], str]


# --------------------------------------------------------------------------- #
# estimation
# --------------------------------------------------------------------------- #
_ENCODER: object = None
_ENCODER_READY = False


def _encoder() -> object:
    """The tiktoken encoder, or ``None`` when the package is absent."""
    global _ENCODER, _ENCODER_READY
    if _ENCODER_READY:
        return _ENCODER
    _ENCODER_READY = True
    try:
        import tiktoken

        _ENCODER = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - any failure means "estimate by hand"
        _ENCODER = None
    return _ENCODER


def estimate_tokens(text: str) -> int:
    """Tokens in ``text``: tiktoken when available, four characters otherwise."""
    if not text:
        return 0
    encoder = _encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - fall through to the estimate
            pass
    return max(1, len(text) // CHARS_PER_TOKEN)


def _message_text(message: dict) -> str:
    parts = [str(message.get("content") or "")]
    name = message.get("name")
    if name:
        parts.append(str(name))
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict):
            parts.append(str(function.get("name") or ""))
            parts.append(str(function.get("arguments") or ""))
    return "\n".join(part for part in parts if part)


def estimate_messages(messages: Sequence[dict]) -> int:
    """Tokens for a whole payload, with the per-message overhead providers add."""
    total = 0
    for message in messages:
        total += 4 + estimate_tokens(_message_text(message))
    return total


# --------------------------------------------------------------------------- #
# budget
# --------------------------------------------------------------------------- #
def _ollama_base() -> str:
    from .providers.ollama import base_url

    return base_url()


#: model -> (when we asked, window). A miss is remembered for a minute: the probe
#: costs a second when Ollama is down, and sessions are created per turn.
_NATIVE: Dict[str, "tuple[float, Optional[int]]"] = {}
_MISS_TTL = 60.0


def native_context(model: str, timeout: float = 1.0) -> Optional[int]:
    """The model's window, from Ollama's ``POST /api/show``. ``None`` if unknown.

    Not ``GET /api/tags``: the list endpoint carries names and sizes only. The
    window lives in ``model_info``, under a key whose prefix is the architecture
    (``llama.context_length``, ``qwen2.context_length``, …).
    """
    name = (model or "").strip()
    if not name or "/" in name and name.count("/") > 1:
        return None
    cached = _NATIVE.get(name)
    if cached is not None and time.monotonic() - cached[0] < _MISS_TTL:
        return cached[1]  # already Optional[int]
    short = name.split(":", 1)[0] if name.count(":") == 1 else name
    value = None
    for candidate in (name, short):
        value = _show_context(candidate, timeout)
        if value:
            break
    _NATIVE[name] = (time.monotonic(), value)
    return value


def _show_context(model: str, timeout: float) -> Optional[int]:
    import httpx

    try:
        response = httpx.post(
            f"{_ollama_base()}/api/show", json={"model": model}, timeout=timeout
        )
    except Exception:  # noqa: BLE001 - Ollama down is a normal state
        return None
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    info = payload.get("model_info") if isinstance(payload, dict) else None
    if not isinstance(info, dict):
        return None
    for key, value in info.items():
        if key.endswith(".context_length") and isinstance(value, int) and value > 0:
            return value
    return None


def budget_from_env() -> Optional[int]:
    raw = (os.environ.get("UH_CONTEXT_BUDGET") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def budget_from_config(cfg: object) -> Optional[int]:
    getter = getattr(cfg, "get", None)
    if getter is None:
        return None
    try:
        value = int(getter("context_budget_tokens") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def default_budget(
    model: Optional[str] = None, cfg: object = None, native: Optional[int] = None
) -> int:
    """The token budget for this payload, by precedence (see the module docstring).

    The hardware tier is a ceiling chosen for the machine, not for the model: a
    24k budget on a 4k model is how a turn fails, so the tier is capped at 75% of
    the real window whenever that window is known.
    """
    override = budget_from_env() or budget_from_config(cfg)
    if override:
        return override
    window = native if native is not None else (native_context(model or "") if model else None)
    from . import hardware

    tier_budget = hardware.budget_for_tier()
    if window:
        return max(1, int(min(tier_budget, window * NATIVE_RATIO)))
    return tier_budget or DEFAULT_BUDGET


# --------------------------------------------------------------------------- #
# pruning
# --------------------------------------------------------------------------- #
def truncate_tool(message: dict, limit: int = TOOL_LIMIT) -> dict:
    """Copy of ``message`` with a tool result cut to ``limit`` characters."""
    content = message.get("content")
    if message.get("role") != "tool" or not isinstance(content, str):
        return message
    if len(content) <= limit:
        return message
    kept = content[:limit]
    omitted = len(content) - limit
    trimmed = dict(message)
    trimmed["content"] = f"{kept}\n{TRUNCATION.format(omitted=omitted)}"
    return trimmed


def _first_line(message: dict, limit: int = 140) -> str:
    raw = " ".join(str(message.get("content") or "").split())
    if not raw and message.get("tool_calls"):
        names = [
            (call.get("function") or {}).get("name")
            for call in message.get("tool_calls") or []
            if isinstance(call, dict)
        ]
        raw = "calls " + ", ".join(str(name) for name in names if name)
    return raw[:limit] + ("…" if len(raw) > limit else "")


def condense(messages: Sequence[dict], limit: int = 1200) -> str:
    """Deterministic stand-in for a model summary: role + first line, most recent last."""
    lines = [
        f"[summary of {len(messages)} earlier messages, condensed without a model call]"
    ]
    for message in messages:
        role = str(message.get("role") or "?")
        lines.append(f"- {role}: {_first_line(message)}")
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "\n…[summary truncated]"
    return text


SUMMARY_INSTRUCTION = (
    "Summarise this conversation fragment in at most 200 words. Keep decisions, file "
    "paths, numbers, names and anything the user asked to remember. Drop small talk. "
    "Answer with the summary only, no preamble."
)


def summarise_with(provider: object, messages: Sequence[dict], limit: int = TOOL_LIMIT) -> str:
    """Ask ``provider`` for a summary of the dropped messages. Empty on failure.

    Called at most once per distinct middle block, and only when the payload is
    over budget: this is the only place pruning spends a token.
    """
    fragment: List[str] = []
    for message in messages:
        body = _message_text(message)
        if len(body) > limit:
            body = body[:limit] + TRUNCATION.format(omitted=len(body) - limit)
        fragment.append(f"{message.get('role') or '?'}: {body}")
    prompt = SUMMARY_INSTRUCTION + "\n\n" + "\n".join(fragment)
    try:
        response = provider.chat([{"role": "user", "content": prompt}], [])  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - a failed summary falls back to condense()
        return ""
    content = response.get("content") if isinstance(response, dict) else ""
    return str(content or "").strip()


class ContextManager:
    """Holds the budget and the summary cache for one session."""

    def __init__(
        self,
        budget_tokens: int = DEFAULT_BUDGET,
        summarizer: Optional[Summarizer] = None,
        keep_tail: int = KEEP_TAIL,
        tool_limit: int = TOOL_LIMIT,
    ) -> None:
        self.budget = max(1, int(budget_tokens))
        self.summarizer = summarizer
        self.keep_tail = max(1, int(keep_tail))
        self.tool_limit = max(0, int(tool_limit))
        self._signatures: Dict[tuple, str] = {}
        self._last_summary = ""
        self._last_middle: tuple = (0, 0)  # (messages, characters) the summary covers
        self.last_summary = ""
        #: Turns the *used* summary covers, and how the last payload was built:
        #: "none" (it fit), "summary" (a summary replaced the middle) or "trim"
        #: (nothing fit, so the middle was dropped without summarising).
        self.last_summary_turns = 0
        self.last_mode = "none"
        self.last_payload_tokens = 0

    def snapshot(self) -> Dict[str, Any]:
        """What the last prune did: budget, summary and the window it covers.

        Public because the Decision view has to report the pruning as a fact,
        and reaching into ``_last_middle`` from another module would make that
        private state a contract by accident.
        """
        return {
            "budget": self.budget,
            "summary": self.last_summary,
            "summarised_exchanges": int(self.last_summary_turns),
            "kept_exchanges": self.keep_tail,
            "mode": self.last_mode,
            "payload_tokens": self.last_payload_tokens,
        }

    # -- the public entry point -------------------------------------------- #
    def prepare(self, messages: Sequence[dict]) -> List[dict]:
        """Return a payload that fits the budget. Same order, fewer tokens."""
        original = list(messages)
        if estimate_messages(original) <= self.budget:
            self.last_mode = "none"
            return self._sent(original)

        trimmed = [truncate_tool(message, self.tool_limit) for message in original]
        if estimate_messages(trimmed) <= self.budget:
            # Tool results were cut, but nothing was replaced by a summary.
            self.last_mode = "trim"
            self.last_summary = ""
            self.last_summary_turns = 0
            return self._sent(trimmed)

        systems = [message for message in trimmed if message.get("role") == "system"]
        rest = [message for message in trimmed if message.get("role") != "system"]
        if not rest:
            return self._sent(trimmed)

        # Head: the first user message, plus the first assistant when it directly
        # follows (it sets the voice of the conversation).
        head_end = 1
        if len(rest) > 1 and rest[1].get("role") == "assistant":
            head_end = 2

        for tail_size in self._tail_sizes():
            start_tail = len(rest) - tail_size
            if start_tail <= head_end:
                continue  # nothing between head and tail to compress
            middle = rest[head_end:start_tail]
            summary = self._summary(middle)
            candidate = (
                systems
                + rest[:head_end]
                + [{"role": "system", "content": summary}]
                + rest[start_tail:]
            )
            if estimate_messages(candidate) <= self.budget:
                self.last_summary = summary
                self.last_summary_turns = sum(
                    1 for message in middle if message.get("role") == "user"
                )
                self.last_mode = "summary"
                return self._sent(candidate)
        # Nothing fits even at keep_tail=1: hard-trim the oldest survivors. The
        # payload carries no summary, so saying "N exchanges summarised" here
        # would describe something the model never saw.
        self.last_mode = "trim"
        self.last_summary = ""
        self.last_summary_turns = 0
        return self._sent(self._last_resort(systems, rest))

    def _sent(self, payload: List[dict]) -> List[dict]:
        """Remember the size of the payload that goes out.

        The budget is a per-call limit; a turn's usage counters sum several
        calls. Comparing the two would show "over budget" on a turn that was
        never over budget.
        """
        self.last_payload_tokens = estimate_messages(payload)
        return payload

    def _tail_sizes(self) -> List[int]:
        sizes = [self.keep_tail]
        for candidate in (3, 1):
            if candidate < self.keep_tail:
                sizes.append(candidate)
        return sizes

    def _summary(self, middle: Sequence[dict]) -> str:
        """A summary of the dropped middle, reused until it grows meaningfully.

        Two caches, on purpose: an exact signature (same middle, hit within a
        turn) and the last summary plus its size (nearly the same middle, next
        turn). The second one covers the gap where a single new message would
        otherwise trigger a brand new model call, and it says so in the payload
        rather than pretending it summarised everything.
        """
        signature = tuple(
            (str(message.get("role")), estimate_tokens(_message_text(message)))
            for message in middle
        )
        cached = self._signatures.get(signature)
        if cached is not None:
            return cached
        turns = sum(1 for message in middle if message.get("role") == "user")
        chars = sum(len(_message_text(message)) for message in middle)
        if self._last_summary:
            grown_turns = turns - int(self._last_middle[0])
            grown_chars = chars - int(self._last_middle[1])
            if grown_turns < SUMMARY_MIN_TURNS and grown_chars < SUMMARY_MIN_CHARS:
                covered = int(self._last_middle[0])
                reused = self._last_summary
                if grown_turns > 0:
                    reused = (
                        f"{reused}\n[{covered} of {turns} earlier exchanges summarised; "
                        "the rest are older context]"
                    )
                self._signatures[signature] = reused
                return reused
        text = ""
        if self.summarizer is not None:
            try:
                text = (self.summarizer(list(middle)) or "").strip()
            except Exception:  # noqa: BLE001 - a failed summary must not fail the turn
                text = ""
        if not text:
            text = condense(middle)
        self._signatures[signature] = text
        self._last_summary = text
        self._last_middle = (turns, chars)
        return text

    def _last_resort(self, systems: List[dict], rest: List[dict]) -> List[dict]:
        """Keep the system prompt and the last few messages, trimmed to fit."""
        for tail_size in range(min(3, len(rest)), 0, -1):
            tail = [dict(message) for message in rest[-tail_size:]]
            while tail and estimate_messages(systems + tail) > self.budget:
                longest = max(tail, key=lambda message: len(str(message.get("content") or "")))
                content = str(longest.get("content") or "")
                # Each step must strictly shrink, or this loop never ends: below 160
                # characters the marker costs more than the text saves, so the
                # message goes instead of being trimmed again.
                if len(content) <= 160:
                    tail.remove(longest)
                    continue
                keep = max(80, min(len(content) // 2, len(content) - 40))
                longest["content"] = content[:keep] + TRUNCATION.format(
                    omitted=len(content) - keep
                )
            if tail:
                return systems + tail
        return systems


__all__ = [
    "CHARS_PER_TOKEN",
    "ContextManager",
    "DEFAULT_BUDGET",
    "KEEP_TAIL",
    "NATIVE_RATIO",
    "SUMMARY_MIN_CHARS",
    "SUMMARY_MIN_TURNS",
    "TOOL_LIMIT",
    "TRUNCATION",
    "budget_from_config",
    "budget_from_env",
    "condense",
    "default_budget",
    "estimate_messages",
    "estimate_tokens",
    "native_context",
    "summarise_with",
    "truncate_tool",
]
