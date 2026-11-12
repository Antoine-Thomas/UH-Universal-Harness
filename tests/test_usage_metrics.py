"""Usage metrics: the real model, tokens in/out/total, per turn and per session."""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx
import pytest

from uh import config as uh_config
from uh import console
from uh.providers import ProviderError, normalise_usage
from uh.providers.ollama import OllamaProvider
from uh.providers.omnirouter import OmniRouterProvider

URL = "http://127.0.0.1:20128"
OPENAI_USAGE = {"prompt_tokens": 312, "completion_tokens": 922, "total_tokens": 1234}


class CountingProvider:
    """Reports a fixed usage per call, and a served model != configured model."""

    model = "auto/best-free"

    def __init__(
        self,
        usage: Optional[dict] = OPENAI_USAGE,
        served: str = "qwen3:8b",
        error: Optional[Exception] = None,
    ) -> None:
        self.usage = usage
        self.served = served
        self.error = error
        self.last_usage: Optional[dict] = None
        self.last_real_model: Optional[str] = None
        self._total = {"prompt": 0, "completion": 0, "total": 0}

    def usage_snapshot(self) -> dict:
        return dict(self._total)

    def chat(self, messages, tools, *, on_token=None):
        if self.error is not None:
            raise self.error
        if on_token is not None:
            on_token("bonjour")
        self.last_real_model = self.served
        if self.usage:
            self.last_usage = normalise_usage(self.usage)
            for key in self._total:
                self._total[key] += self.last_usage[key]
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


def session_with(provider, tmp_path, *, chain=None, stream: bool = False):
    chain = chain or [("omnirouter", provider)]
    return console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind=chain[0][0],
        chain=chain,
        model="auto/best-free",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=stream,
        store=None,
        max_iterations=2,
    )


# --------------------------------------------------------------------------- #
# normalisation
# --------------------------------------------------------------------------- #
def test_openai_counters_are_normalised() -> None:
    assert normalise_usage(OPENAI_USAGE) == {"prompt": 312, "completion": 922, "total": 1234}


def test_ollama_counters_are_normalised_without_a_total() -> None:
    usage = normalise_usage({"prompt_eval_count": 312, "eval_count": 922})
    assert usage == {"prompt": 312, "completion": 922, "total": 1234}


@pytest.mark.parametrize("raw", [None, {}, "nope", {"model": "x"}, {"prompt_tokens": "?"}])
def test_no_counters_means_none_not_zero(raw: Any) -> None:
    assert normalise_usage(raw) is None


# --------------------------------------------------------------------------- #
# session accumulation and display
# --------------------------------------------------------------------------- #
def test_two_turns_accumulate_and_keep_the_last_one(tmp_path, capsys) -> None:
    provider = CountingProvider()
    session = session_with(provider, tmp_path)

    assert session.run_text("premier") == 0
    assert session.run_text("second") == 0

    capsys.readouterr()
    assert session.last_usage == {"prompt": 312, "completion": 922, "total": 1234}
    assert session.total_usage == {"prompt": 624, "completion": 1844, "total": 2468}
    assert session.last_real_model == "qwen3:8b"


def test_the_metrics_line_is_printed_after_the_turn(tmp_path, capsys) -> None:
    session = session_with(CountingProvider(), tmp_path)

    session.run_text("salut")

    err = capsys.readouterr().err
    assert "omnirouter  qwen3:8b  1234 tok (in 312 / out 922)" in err
    assert "total session 1234 tok" in err


def test_the_line_names_the_served_model_not_the_configured_alias(tmp_path, capsys) -> None:
    session = session_with(CountingProvider(served="deepseek-v4-flash"), tmp_path)

    session.run_text("salut")

    assert "auto/best-free" not in capsys.readouterr().err
    assert session.usage_line().startswith("omnirouter  deepseek-v4-flash")


def test_a_provider_without_counters_says_so(tmp_path, capsys) -> None:
    session = session_with(CountingProvider(usage=None), tmp_path)

    session.run_text("salut")

    err = capsys.readouterr().err
    assert "tokens non rapportés" in err
    assert session.last_usage is None
    assert session.total_usage == {"prompt": 0, "completion": 0, "total": 0}
    assert session.last_real_model == "qwen3:8b", "the model is known even without usage"


def test_status_reports_the_served_model_and_the_cumulative(tmp_path) -> None:
    session = session_with(CountingProvider(), tmp_path)
    session.run_text("salut")

    status = session.status()
    assert "served:   qwen3:8b" in status
    assert "tokens:   last 1234 tok (in 312 / out 922), session 1234 tok" in status


def test_a_fallback_only_counts_the_provider_that_answered(tmp_path, capsys) -> None:
    failing = CountingProvider(error=ProviderError("model does not support tools", status=400))
    answering = CountingProvider(usage={"prompt_tokens": 10, "completion_tokens": 5})
    session = session_with(failing, tmp_path, chain=[("ollama", failing), ("deepseek", answering)])

    assert session.run_text("salut") == 0

    assert session.last_usage == {"prompt": 10, "completion": 5, "total": 15}
    assert session.last_real_model == "qwen3:8b"
    assert "fallback: ollama -> deepseek" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# provider parsing (blocking)
# --------------------------------------------------------------------------- #
def _blocking_payload(**extra) -> dict:
    return {"choices": [{"message": {"content": "bonjour"}}], **extra}


def test_omnirouter_blocking_reads_model_and_usage(monkeypatch) -> None:
    payload = _blocking_payload(model="qwen3:8b", usage=OPENAI_USAGE)
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: type("R", (), {"status_code": 200, "text": "", "json": lambda s: payload})(),
    )

    provider = OmniRouterProvider(model="auto/best-free", base_url=URL, api_key="k")
    provider.chat([{"role": "user", "content": "salut"}], [])

    assert provider.last_real_model == "qwen3:8b"
    assert provider.last_usage == {"prompt": 312, "completion": 922, "total": 1234}
    assert provider.usage_snapshot() == {"prompt": 312, "completion": 922, "total": 1234}


def test_ollama_blocking_reads_its_own_counters(monkeypatch) -> None:
    payload = {
        "model": "tinyllama:latest",
        "message": {"content": "bonjour", "tool_calls": []},
        "prompt_eval_count": 26,
        "eval_count": 297,
    }
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: type("R", (), {"status_code": 200, "text": "", "json": lambda s: payload})(),
    )

    provider = OllamaProvider("tinyllama:latest", base_url_override="http://127.0.0.1:11434")
    provider.chat([{"role": "user", "content": "salut"}], [])

    assert provider.last_real_model == "tinyllama:latest"
    assert provider.last_usage == {"prompt": 26, "completion": 297, "total": 323}


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #
class FakeStream:
    def __init__(self, status: int, lines: list[str]) -> None:
        self.status_code = status
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self):
        return iter(self._lines)

    def iter_text(self):
        return iter(["".join(self._lines)])


SSE_WITH_USAGE = [
    'data: {"model":"qwen3:8b","choices":[{"delta":{"content":"Bonjour"}}]}',
    'data: {"model":"qwen3:8b","choices":[],"usage":{"prompt_tokens":7,"completion_tokens":3,'
    '"total_tokens":10}}',
    "data: [DONE]",
]


def test_streaming_asks_for_usage_and_reads_the_final_chunk(monkeypatch) -> None:
    payloads: list[dict] = []

    def fake_stream(method, url, **kwargs):
        payloads.append(kwargs.get("json") or {})
        return FakeStream(200, SSE_WITH_USAGE)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    provider = OmniRouterProvider(model="auto/best-free", base_url=URL, api_key="k")
    tokens: list[str] = []

    provider.chat([{"role": "user", "content": "salut"}], [], on_token=tokens.append)

    assert tokens == ["Bonjour"]
    assert payloads[0]["stream_options"] == {"include_usage": True}
    assert provider.last_usage == {"prompt": 7, "completion": 3, "total": 10}
    assert provider.last_real_model == "qwen3:8b"


def test_a_gateway_refusing_stream_options_is_retried_without_it(monkeypatch) -> None:
    payloads: list[dict] = []
    attempts = {"n": 0}

    def fake_stream(method, url, **kwargs):
        payloads.append(kwargs.get("json") or {})
        attempts["n"] += 1
        if attempts["n"] == 1:
            return FakeStream(400, ["unknown parameter: stream_options"])
        return FakeStream(200, SSE_WITH_USAGE)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    provider = OmniRouterProvider(model="auto/best-free", base_url=URL, api_key="k")
    tokens: list[str] = []

    message = provider.chat([{"role": "user", "content": "salut"}], [], on_token=tokens.append)

    assert message["content"] == "Bonjour"
    assert "stream_options" in payloads[0]
    assert "stream_options" not in payloads[1], "the retry drops the parameter"
    assert provider.last_usage == {"prompt": 7, "completion": 3, "total": 10}


def test_a_stream_that_breaks_after_tokens_is_not_retried(monkeypatch) -> None:
    """Once text is on screen, a failure is reported, never replayed."""
    payloads: list[dict] = []
    lines = [
        'data: {"choices":[{"delta":{"content":"Bon"}}]}',
        'data: {"error":{"message":"upstream reset"}}',
    ]

    def fake_stream(method, url, **kwargs):
        payloads.append(kwargs.get("json") or {})
        return FakeStream(200, lines)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    provider = OmniRouterProvider(model="auto/best-free", base_url=URL, api_key="k")
    tokens: list[str] = []

    with pytest.raises(ProviderError):
        provider.chat([{"role": "user", "content": "salut"}], [], on_token=tokens.append)

    assert tokens == ["Bon"]
    assert len(payloads) == 1, "no second request after a partial answer"


def test_ollama_streaming_reads_the_done_event(monkeypatch) -> None:
    lines = [
        json.dumps({"message": {"content": "Bon"}, "done": False}),
        json.dumps(
            {
                "message": {"content": ""},
                "done": True,
                "model": "tinyllama:latest",
                "prompt_eval_count": 26,
                "eval_count": 297,
            }
        ),
    ]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: FakeStream(200, lines))
    provider = OllamaProvider("tinyllama:latest", base_url_override="http://127.0.0.1:11434")

    provider.chat([{"role": "user", "content": "salut"}], [], on_token=lambda _t: None)

    assert provider.last_real_model == "tinyllama:latest"
    assert provider.last_usage == {"prompt": 26, "completion": 297, "total": 323}
