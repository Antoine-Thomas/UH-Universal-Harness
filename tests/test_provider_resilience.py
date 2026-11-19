"""P1-9: what happens when the network dies between two tokens.

The user-visible contract, in order:

1. a transport failure is retried on the SAME provider, unless a token already
   reached the screen (replaying a started answer duplicates it);
2. the chain moves to the next provider on a transport failure, says so in the
   transcript, and **never** does that for a refused request (401, 404, 400) -
   a wrong key is not an incident, and answering from another account hides it;
3. an answer cut mid-stream is kept, marked, stored, and the browser is told the
   words on screen are real (`partial: true`) so it can offer "Continuer".
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
import pytest

from uh import config as uh_config
from uh import console
from uh.providers import Provider, ProviderError, retry_transport
from uh.providers.ollama import OllamaProvider
from uh.store import Store


# --------------------------------------------------------------------------- #
# the retry helper
# --------------------------------------------------------------------------- #
def test_a_transport_failure_is_retried_until_it_works() -> None:
    calls = {"n": 0}

    def send() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("connection reset", kind="transport")
        return "ok"

    slept: list[float] = []
    assert retry_transport(send, label="x", sleep=slept.append, backoff=0.5) == "ok"
    assert calls["n"] == 3
    assert slept == [0.5, 1.0], "backoff doubles, and the last failure is not slept on"


def test_a_refused_request_is_not_retried() -> None:
    calls = {"n": 0}

    def send() -> str:
        calls["n"] += 1
        raise ProviderError("no", status=401)

    with pytest.raises(ProviderError):
        retry_transport(send, label="x", sleep=lambda _s: None)
    assert calls["n"] == 1, "a bad key does not get better by asking again"


def test_a_stream_that_already_spoke_is_never_replayed() -> None:
    calls = {"n": 0}
    spoken = {"any": False}

    def send() -> str:
        calls["n"] += 1
        spoken["any"] = True
        raise ProviderError("reset", kind="transport")

    with pytest.raises(ProviderError):
        retry_transport(send, label="x", emitted=lambda: spoken["any"], sleep=lambda _s: None)
    assert calls["n"] == 1, "replaying would print the same sentence twice"


def test_three_attempts_then_give_up() -> None:
    calls = {"n": 0}

    def send() -> str:
        calls["n"] += 1
        raise ProviderError("reset", kind="transport")

    with pytest.raises(ProviderError):
        retry_transport(send, label="x", sleep=lambda _s: None, attempts=3)
    assert calls["n"] == 3


# --------------------------------------------------------------------------- #
# a real provider, one blip
# --------------------------------------------------------------------------- #
class FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"message": {"content": "bonjour", "tool_calls": []}, "model": "m"}


def test_ollama_survives_one_dropped_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("[WinError 10054] the peer closed the connection")
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", flaky)
    provider = OllamaProvider("m", base_url_override="http://127.0.0.1:11434")
    provider.retry_sleep = lambda _s: None

    message = provider.chat([{"role": "user", "content": "salut"}], [])

    assert calls["n"] == 2, "the second attempt is what made this answer exist"
    assert message["content"] == "bonjour"


# --------------------------------------------------------------------------- #
# the chain
# --------------------------------------------------------------------------- #
class Stub(Provider):
    name = "stub"
    model = "stub:1b"

    def __init__(self, label: str, failure: Optional[Exception] = None) -> None:
        super().__init__()
        self.label = label
        self.failure = failure
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        if on_token is not None:
            on_token("bonjour")
        return {"role": "assistant", "content": "bonjour", "tool_calls": []}


def test_the_chain_moves_on_when_the_transport_dies() -> None:
    first = Stub("deepseek", ProviderError("reset", kind="transport"))
    second = Stub("omniroute")
    notices: list[str] = []
    chain = console._ChainProvider([("deepseek", first), ("omniroute", second)])
    chain.on_notice = notices.append

    assert chain.chat([{"role": "user", "content": "x"}], [])["content"] == "bonjour"
    assert first.calls == 1 and second.calls == 1
    assert notices and "Bascule" in notices[0], "the transcript says which provider answered"
    assert "deepseek" in notices[0] and "omniroute" in notices[0]


def test_the_chain_does_not_move_on_for_a_refused_request() -> None:
    first = Stub("deepseek", ProviderError("invalid api key", status=401))
    second = Stub("omniroute")
    chain = console._ChainProvider([("deepseek", first), ("omniroute", second)])

    with pytest.raises(ProviderError) as caught:
        chain.chat([{"role": "user", "content": "x"}], [])
    assert caught.value.status == 401
    assert second.calls == 0, "a wrong key is not a reason to answer with another account"


def test_a_throttled_provider_is_a_fallback_not_a_refusal() -> None:
    first = Stub("deepseek", ProviderError("slow down", status=429))
    second = Stub("omniroute")
    chain = console._ChainProvider([("deepseek", first), ("omniroute", second)])

    assert chain.chat([{"role": "user", "content": "x"}], [])["content"] == "bonjour"
    assert second.calls == 1


def test_a_capability_refusal_still_falls_back() -> None:
    """`400 model does not support tools` describes that provider, not the request.

    This is the product's local-first promise: a local model without tool support
    must not block a turn answered by the cloud. Only credential and billing
    errors (401/402/403) stop the chain.
    """
    first = Stub("ollama", ProviderError("model does not support tools", status=400))
    second = Stub("deepseek")
    chain = console._ChainProvider([("ollama", first), ("deepseek", second)])

    assert chain.chat([{"role": "user", "content": "x"}], [])["content"] == "bonjour"
    assert second.calls == 1


# --------------------------------------------------------------------------- #
# the cut answer
# --------------------------------------------------------------------------- #
class CutMidStream(Provider):
    """Streams two tokens, then the connection dies."""

    name = "cut"
    model = "stub:1b"

    def __init__(self) -> None:
        super().__init__()

    def chat(self, messages, tools, *, on_token=None):
        if on_token is not None:
            on_token("Le théorème ")
            on_token("se démontre")
            raise ProviderError("transport: connection reset", kind="transport")
        raise AssertionError("this test needs the streaming path")


def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider, store=None, sid=None):
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    return console.ChatSession(
        provider_factory=lambda kind, model: provider,
        provider_kind="stub",
        chain=[("stub", provider)],
        model="stub:1b",
        toolsets={},
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=True,
        store=store,
        session_id=sid,
        max_iterations=2,
        console=console.Console(),
    )


def test_a_cut_answer_is_kept_marked_and_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = Store(tmp_path / "uh.db")
    session_id = store.create_session(title="t", model="stub:1b", provider="stub")
    frames: list[dict] = []
    session = _session(tmp_path, monkeypatch, CutMidStream(), store=store, sid=session_id)
    session.events = frames.append

    assert session.run_text("démontre-le") == 1

    errors = [frame for frame in frames if frame.get("type") == "error"]
    assert errors and errors[0]["partial"] is True, "the browser must know the words are real"
    assert errors[0]["source"] == "provider"

    stored = store.get_messages(session_id)
    assistant = [m for m in stored if m["role"] == "assistant"]
    assert assistant, "the partial answer survives a reload"
    assert "se démontre" in assistant[0]["content"]
    assert console.PARTIAL_MARK in assistant[0]["content"], "and it does not read as finished"
    store.close()


def test_an_empty_failure_stores_no_ghost_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Dead(Provider):
        name = "dead"
        model = "stub:1b"

        def chat(self, messages, tools, *, on_token=None):
            raise ProviderError("connection refused", kind="transport")

    store = Store(tmp_path / "uh.db")
    session_id = store.create_session(title="t", model="stub:1b", provider="stub")
    frames: list[dict] = []
    session = _session(tmp_path, monkeypatch, Dead(), store=store, sid=session_id)
    session.events = frames.append

    session.run_text("bonjour")

    errors = [frame for frame in frames if frame.get("type") == "error"]
    assert errors and errors[0]["partial"] is False
    stored = store.get_messages(session_id)
    roles = [m["role"] for m in stored]
    # No ghost answer: nothing claims the model spoke. What is stored is the
    # question plus the sentence that explains the silence (point 1) — the error
    # bubble itself is live only, so without this row a reload would show a
    # question and nothing else.
    assert roles == ["user", "system"], "the question is kept, and the silence is explained"
    assert not [m for m in stored if m["role"] == "assistant"], "no empty answer is invented"
    assert stored[-1]["content"] == console.PROVIDER_FAILED_NOTICE.format(error="ProviderError")
    store.close()


# --------------------------------------------------------------------------- #
# the measurement the lot asks for: 20 forced drops
# --------------------------------------------------------------------------- #
def test_twenty_dropped_connections_cost_no_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before: every drop killed the turn. After: each is absorbed by one retry.

    Counted on 20 turns, every other provider call forced to drop, through the
    real ``OllamaProvider`` (the retry lives in the provider, not in the test):
    lost turns, provider calls, and answers that appear twice.
    """
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise httpx.ConnectError("[WinError 10054] the peer closed the connection")
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", flaky)
    provider = OllamaProvider("m", base_url_override="http://127.0.0.1:11434")
    provider.retry_sleep = lambda _s: None
    session = _session(tmp_path, monkeypatch, provider)
    session.stream = False

    lost = 0
    frames: list[dict] = []
    for _ in range(20):
        frame_sink: list[dict] = []
        session.events = frame_sink.append
        if session.run_text("question") != 0:
            lost += 1
        frames.extend(frame_sink)
        session.history.clear()

    assert lost == 0, f"{lost} turn(s) lost on 20 forced drops"
    assert calls["n"] == 40, "one retry per drop, never more"
    answers = [frame for frame in frames if frame.get("type") == "answer"]
    assert len(answers) == 20, "no answer was duplicated by a retry"
    assert all(answer["content"] == "bonjour" for answer in answers)
