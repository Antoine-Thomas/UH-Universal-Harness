"""Fallback chain: priority, key gating, 429 handover, clear refusal.

No network: every provider is a stub, and only the chain logic is exercised.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from uh import cli
from uh import config as uh_config
from uh.console import ChatSession, _ChainProvider
from uh.providers import ProviderError
from uh.providers.deepseek import DeepSeekProvider
from uh.toolsets import build_toolsets

ENV_VARS = (
    "UH_CLOUDFLARE_ACCOUNT_ID",
    "UH_CLOUDFLARE_API_TOKEN",
    "UH_DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY",
    "UH_OMNIROUTER_URL",
    "OMNIROUTE_BASE_URL",
    "UH_OMNIROUTER_API_KEY",
    "OMNIROUTE_API_KEY",
)


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UH_OMNIROUTER_CONFIG", str(tmp_path / "no-omnirouter.env"))


class StubProvider:
    def __init__(self, answer: str = "ok", model: str = "stub:1b") -> None:
        self.answer = answer
        self.model = model
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if on_token is not None:
            on_token(self.answer)
        return {"role": "assistant", "content": self.answer, "tool_calls": []}


class FailingProvider:
    model = "stub:1b"

    def __init__(self, status: int = 429, text: str = "rate limited") -> None:
        self.status = status
        self.text = text
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        raise ProviderError(
            f"Cloudflare Workers AI HTTP {self.status}: {self.text}", status=self.status
        )


def stub_factory(kind: str, model: str) -> StubProvider:
    return StubProvider(model=model or f"{kind}:1b")


def session_with(chain, tmp_path: Path) -> ChatSession:
    return ChatSession(
        provider_factory=stub_factory,
        provider_kind=chain[0][0],
        chain=chain,
        model="stub:1b",
        toolsets=build_toolsets(tmp_path),
        workspace=tmp_path,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=2,
    )


# --------------------------------------------------------------------------- #
# chain order and key gating
# --------------------------------------------------------------------------- #
def test_default_order_is_ollama_cloudflare_deepseek_omnirouter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "list_models", lambda: [{"name": "llama3:8b", "size": 1, "modified_at": ""}])
    monkeypatch.setattr(cli, "_make_provider", stub_factory)
    monkeypatch.setenv("UH_CLOUDFLARE_ACCOUNT_ID", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("UH_CLOUDFLARE_API_TOKEN", "cf-fallback-token-1234")
    monkeypatch.setenv("UH_DEEPSEEK_API_KEY", "ds-fallback-key-5678")

    chain, model = cli._build_chain(None, None)
    assert [kind for kind, _ in chain] == ["ollama", "cloudflare", "deepseek", "omnirouter"]
    assert model == "llama3:8b"


def test_missing_cloudflare_credentials_skip_straight_to_deepseek(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "list_models", lambda: [])  # Ollama up, no model
    monkeypatch.setattr(cli, "_make_provider", stub_factory)
    monkeypatch.setenv("UH_DEEPSEEK_API_KEY", "ds-fallback-key-5678")

    chain, _ = cli._build_chain(None, None)
    assert [kind for kind, _ in chain] == ["deepseek", "omnirouter"]
    err = capsys.readouterr().err
    assert "fallback: ollama -> deepseek" in err


def test_preferred_provider_moves_to_the_front_without_dropping_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "list_models", lambda: [{"name": "llama3:8b", "size": 1, "modified_at": ""}])
    monkeypatch.setattr(cli, "_make_provider", stub_factory)
    monkeypatch.setenv("UH_DEEPSEEK_API_KEY", "ds-fallback-key-5678")

    # Since the local-first policy, a configured default orders the cloud entries
    # only: a running local model stays in front. UH_ROUTER_PREFER=cloud restores
    # the old order, and that is what this test now checks.
    monkeypatch.setenv("UH_ROUTER_PREFER", "cloud")
    chain, _ = cli._build_chain(None, None, preferred="deepseek")
    assert [kind for kind, _ in chain] == ["deepseek", "omnirouter", "ollama"]


def test_no_provider_configured_raises_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "list_models", lambda: [])

    with pytest.raises(ProviderError) as failure:
        cli._build_chain(None, None)
    message = str(failure.value)
    assert "no usable provider" in message
    assert "cloudflare: not configured" in message
    assert "deepseek: not configured" in message


# --------------------------------------------------------------------------- #
# runtime handover
# --------------------------------------------------------------------------- #
def test_429_on_cloudflare_falls_back_to_deepseek(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    failing = FailingProvider(429)
    answering = StubProvider(answer="réponse deepseek")
    session = session_with([("cloudflare", failing), ("deepseek", answering)], tmp_path)

    assert session.run_text("bonjour") == 0
    out = capsys.readouterr()
    assert "réponse deepseek" in out.out
    assert "fallback: cloudflare -> deepseek (reason: 429 rate limit)" in out.err
    assert failing.calls == 1 and answering.calls == 1


def test_401_is_reported_as_an_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    failing = FailingProvider(401, "bad key")
    answering = StubProvider(answer="ok from deepseek")
    session = session_with([("cloudflare", failing), ("deepseek", answering)], tmp_path)

    assert session.run_text("bonjour") == 0
    err = capsys.readouterr().err
    assert "fallback: cloudflare -> deepseek (reason: 401 auth)" in err


def test_every_provider_failing_raises_with_the_attempts(tmp_path: Path) -> None:
    chain = _ChainProvider(
        [("cloudflare", FailingProvider(429)), ("deepseek", FailingProvider(503, "down"))]
    )
    with pytest.raises(ProviderError) as failure:
        chain.chat([{"role": "user", "content": "x"}], [])
    message = str(failure.value)
    assert "all providers failed" in message
    assert "cloudflare" in message and "deepseek" in message


class PartialProvider:
    """Streams one token, then dies: the classic mid-stream failure."""

    model = "stub"
    streaming = True

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if on_token is not None:
            on_token("début de réponse ")
        raise ProviderError("Cloudflare Workers AI broke mid-stream", status=502)


def test_a_started_stream_is_never_replayed_on_the_next_provider(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    partial = PartialProvider()
    backup = StubProvider(answer="réponse complète")
    session = session_with([("cloudflare", partial), ("deepseek", backup)], tmp_path)
    session.stream = True

    assert session.run_text("bonjour") == 1
    out = capsys.readouterr()
    assert out.out.count("début de réponse") == 1  # shown once, not replayed
    assert "réponse complète" not in out.out
    assert backup.calls == 0
    assert "provider error" in out.err


# --------------------------------------------------------------------------- #
# the DeepSeek provider itself
# --------------------------------------------------------------------------- #
def test_deepseek_uses_the_documented_chat_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "bonjour"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    message = DeepSeekProvider(api_key="ds-test-key").chat(
        [{"role": "user", "content": "salut"}], []
    )
    assert message["content"] == "bonjour"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer ds-test-key"


def test_deepseek_without_a_key_names_the_variable() -> None:
    with pytest.raises(ProviderError, match="UH_DEEPSEEK_API_KEY"):
        DeepSeekProvider()
