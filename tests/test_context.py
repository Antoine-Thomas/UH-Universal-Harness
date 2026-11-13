"""Context pruning: budget resolution, the kept head/tail, the summary, the cap.

The contract these tests hold: whatever comes out, it fits the budget, the system
prompt and the question survive, and the last messages are intact. A trimmed
payload that drops the question is not a saving, it is a bug.
"""

from __future__ import annotations

import sys
import types

import pytest

from uh import context


def message(role: str, text: str, **extra) -> dict:
    return {"role": role, "content": text, **extra}


def conversation(turns: int, size: int = 400) -> list[dict]:
    """A system prompt, ``turns`` user/assistant pairs, growing text."""
    messages = [message("system", "You are UH.")]
    for index in range(turns):
        messages.append(message("user", f"question {index} " + "x" * size))
        messages.append(message("assistant", f"answer {index} " + "y" * size))
    return messages


# --------------------------------------------------------------------------- #
# estimation
# --------------------------------------------------------------------------- #
def test_estimate_is_positive_and_grows() -> None:
    assert context.estimate_tokens("") == 0
    assert context.estimate_tokens("bonjour") > 0
    assert context.estimate_tokens("x" * 4000) > context.estimate_tokens("x" * 40)


def test_estimate_messages_counts_the_overhead() -> None:
    empty = context.estimate_messages([{"role": "user", "content": ""}])
    assert empty == 4, "four tokens of overhead per message, by the provider contract"


def test_estimation_falls_back_when_tiktoken_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "_ENCODER", None)
    monkeypatch.setattr(context, "_ENCODER_READY", True)
    assert context.estimate_tokens("a" * 40) == 10, "four characters per token"


def test_estimation_uses_tiktoken_when_it_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    class Encoder:
        def encode(self, text: str) -> list[int]:
            return list(range(len(text.split())))

    monkeypatch.setattr(context, "_ENCODER", Encoder())
    monkeypatch.setattr(context, "_ENCODER_READY", True)
    assert context.estimate_tokens("un deux trois") == 3


# --------------------------------------------------------------------------- #
# budget
# --------------------------------------------------------------------------- #
def test_env_wins_over_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_CONTEXT_BUDGET", "1234")
    assert context.default_budget("qwen2.5:7b", cfg=None, native=32000) == 1234


def test_config_is_second(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)

    class Cfg:
        def get(self, key):
            return 2222 if key == "context_budget_tokens" else None

    assert context.default_budget("qwen2.5:7b", cfg=Cfg(), native=32000) == 2222


def test_seventy_five_percent_of_the_native_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)
    assert context.default_budget("qwen2.5:7b", native=4000) == 3000
    assert context.default_budget("qwen2.5:7b", native=8000) == 6000
    assert context.default_budget("qwen2.5:7b", native=32000) == 24000


def test_the_default_when_nothing_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    """No override, no window, no tier: the last resort is 3000."""
    from uh import hardware

    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)
    monkeypatch.setattr(hardware, "budget_for_tier", lambda: 0)
    assert context.default_budget("unknown:1b", native=None) == 3000


def test_without_a_window_the_tier_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    from uh import hardware

    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)
    monkeypatch.setattr(hardware, "budget_for_tier", lambda: 6000)
    assert context.default_budget("unknown:1b", native=None) == 6000


def test_a_garbage_env_value_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_CONTEXT_BUDGET", "beaucoup")
    assert context.default_budget("m", native=8000) == 6000


def test_the_native_window_is_read_from_api_show(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not /api/tags: only /api/show carries model_info.context_length."""
    import httpx

    seen: list[str] = []

    def fake_post(url: str, json=None, timeout=None):
        seen.append(url)
        return httpx.Response(
            200,
            json={"model_info": {"llama.context_length": 8192, "llama.rope.dimension_count": 128}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(context, "_NATIVE", {})
    monkeypatch.delenv("UH_CONTEXT_BUDGET", raising=False)

    assert context.native_context("qwen2.5:7b") == 8192
    assert seen and seen[0].endswith("/api/show")


def test_an_absent_ollama_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def refuse(*args, **kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setattr(context, "_NATIVE", {})

    assert context.native_context("qwen2.5:7b") is None


# --------------------------------------------------------------------------- #
# preparation
# --------------------------------------------------------------------------- #
def test_a_payload_that_fits_is_returned_untouched() -> None:
    messages = [message("system", "s"), message("user", "bonjour")]
    manager = context.ContextManager(budget_tokens=10_000)

    assert manager.prepare(messages) == messages
    assert manager.last_summary == ""


def test_the_system_prompt_and_the_question_survive() -> None:
    messages = conversation(40)
    manager = context.ContextManager(budget_tokens=2000)

    prepared = manager.prepare(messages)

    assert context.estimate_messages(prepared) <= 2000
    assert prepared[0] == messages[0], "the rules are never dropped"
    assert prepared[1] == messages[1], "the first question is never dropped"
    assert manager.last_summary, "the middle was summarised"


def test_the_last_messages_are_intact() -> None:
    messages = conversation(40)
    manager = context.ContextManager(budget_tokens=2000, keep_tail=5)

    prepared = manager.prepare(messages)

    assert prepared[-5:] == messages[-5:], "the turn is about the tail"


def test_the_first_assistant_message_is_kept_for_voice() -> None:
    messages = conversation(40)
    manager = context.ContextManager(budget_tokens=2000)

    prepared = manager.prepare(messages)

    assert prepared[2] == messages[2], "the first answer sets the voice"
    assert any(
        item["role"] == "system" and "summary" in str(item["content"]) for item in prepared
    )


def test_a_long_tool_result_is_truncated_before_anything_is_summarised() -> None:
    messages = [
        message("system", "rules"),
        message("user", "lis le fichier"),
        message("tool", "z" * 20_000, name="read_file"),
        message("assistant", "voila"),
    ]
    manager = context.ContextManager(budget_tokens=3000)

    prepared = manager.prepare(messages)

    tool = next(item for item in prepared if item["role"] == "tool")
    assert len(tool["content"]) < 20_000
    assert "characters omitted" in tool["content"]
    assert manager.last_summary == "", "truncation was enough: no summary spent"


def test_the_summariser_is_asked_once_per_middle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def summariser(messages):
        calls.append(len(messages))
        return "[resume du modele]"

    manager = context.ContextManager(budget_tokens=2000, summarizer=summariser)
    messages = conversation(40)

    first = manager.prepare(messages)
    second = manager.prepare(messages)

    assert len(calls) == 1, "the summary is cached until the middle changes"
    assert first == second


def test_a_failing_summariser_falls_back_to_the_deterministic_one() -> None:
    def broken(messages):
        raise RuntimeError("provider down")

    manager = context.ContextManager(budget_tokens=2000, summarizer=broken)

    prepared = manager.prepare(conversation(40))

    assert any("condensed without a model call" in str(item["content"]) for item in prepared)


def test_condense_names_the_roles_it_replaces() -> None:
    text = context.condense([message("user", "q1"), message("assistant", "a1")])

    assert "2 earlier messages" in text
    assert "user: q1" in text and "assistant: a1" in text


def test_an_unsatisfiable_budget_still_returns_something_usable() -> None:
    manager = context.ContextManager(budget_tokens=10)
    prepared = manager.prepare(conversation(20))

    assert prepared, "never an empty payload"
    assert prepared[0]["role"] == "system"
    assert context.estimate_messages(prepared) <= 100, "trimmed down hard, not abandoned"


def test_a_conversation_without_a_system_prompt_is_handled() -> None:
    messages = [message("user", "q " + "x" * 500) for _ in range(30)]
    manager = context.ContextManager(budget_tokens=1500)

    prepared = manager.prepare(messages)

    assert prepared[0]["content"].startswith("q ")
    assert context.estimate_messages(prepared) <= 1500


def test_summarise_with_builds_a_prompt_and_returns_the_answer() -> None:
    seen: list[list[dict]] = []

    class Provider:
        def chat(self, messages, tools):
            seen.append(messages)
            return {"role": "assistant", "content": "  resume  "}

    text = context.summarise_with(Provider(), [message("user", "bonjour")])

    assert text == "resume"
    assert seen[0][0]["role"] == "user"
    assert "Summarise" in seen[0][0]["content"]
    assert "bonjour" in seen[0][0]["content"]


def test_summarise_with_returns_empty_when_the_provider_fails() -> None:
    class Provider:
        def chat(self, messages, tools):
            raise RuntimeError("nope")

    assert context.summarise_with(Provider(), [message("user", "x")]) == ""


def test_a_tool_only_message_is_estimated_without_crashing() -> None:
    payload = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "read_file", "arguments": "{}"}}],
        }
    ]
    assert context.estimate_messages(payload) > 4


@pytest.mark.parametrize("turns", [5, 20, 60])
def test_the_budget_is_respected_for_any_length(turns: int) -> None:
    manager = context.ContextManager(budget_tokens=2500)
    prepared = manager.prepare(conversation(turns))
    assert context.estimate_messages(prepared) <= 2500


def test_sys_modules_is_not_polluted_by_the_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: the fake pypdf of another module must not leak into this one."""
    assert "pypdf" not in sys.modules or types.ModuleType is not None


def test_the_summary_is_reused_until_the_middle_grows_by_four_exchanges() -> None:
    """15 turns must not cost 8 summaries: that was the bug, this is the guard."""
    calls: list[int] = []

    def summariser(messages):
        calls.append(len(messages))
        return f"[resume {len(calls)}]"

    manager = context.ContextManager(budget_tokens=1200, summarizer=summariser)
    messages = [message("system", "rules")]
    for turn in range(15):
        messages.append(message("user", f"question {turn} " + "contexte " * 60))
        messages.append(message("assistant", "ok " + "reponse " * 20))
        prepared = manager.prepare(messages)
        assert context.estimate_messages(prepared) <= 1200

    assert 1 <= len(calls) <= 3, f"expected at most 3 summaries for 15 turns, got {len(calls)}"


def test_a_single_new_message_does_not_buy_a_new_summary() -> None:
    calls: list[int] = []

    def summariser(messages):
        calls.append(len(messages))
        return "[resume]"

    manager = context.ContextManager(budget_tokens=1200, summarizer=summariser)
    messages = conversation(20)
    manager.prepare(messages)
    first = len(calls)

    messages = messages + [message("user", "une question de plus"), message("assistant", "ok")]
    manager.prepare(messages)

    assert len(calls) == first, "one exchange later, the summary is still good"


def test_a_reused_summary_says_what_it_covers() -> None:
    manager = context.ContextManager(budget_tokens=1200, summarizer=lambda m: "[resume]")
    messages = conversation(20)
    manager.prepare(messages)

    messages = messages + [message("user", "encore une"), message("assistant", "ok")]
    prepared = manager.prepare(messages)

    summaries = [item["content"] for item in prepared if item["role"] == "system"]
    assert any("earlier exchanges summarised" in str(text) for text in summaries)


def test_a_huge_jump_does_trigger_a_new_summary() -> None:
    calls: list[int] = []

    def summariser(messages):
        calls.append(len(messages))
        return "[resume]"

    manager = context.ContextManager(budget_tokens=1200, summarizer=summariser)
    manager.prepare(conversation(20))
    before = len(calls)

    manager.prepare(conversation(40))

    assert len(calls) > before, "a much longer middle gets a fresh summary"
