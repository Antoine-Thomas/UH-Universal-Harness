"""The router's order: local first when it exists, cloud always behind it."""

from __future__ import annotations

import httpx
import pytest


# --------------------------------------------------------------------------- #
# local first, cloud second
# --------------------------------------------------------------------------- #
def _chain_kinds(monkeypatch: pytest.MonkeyPatch, *, ollama_up: bool, prefer: str | None = None):
    from uh import cli, providers

    # The chain skips a provider that is not configured, and "configured" used to
    # mean "this machine has ~/.omniroute/.env": the test passed on the maintainer's
    # Windows box and failed on Linux, where no gateway config exists. A test states
    # what it needs.
    monkeypatch.setenv("UH_OMNIROUTER_URL", "http://127.0.0.1:20128")
    monkeypatch.setattr(cli, "list_models", lambda: [{"name": "qwen3:8b"}] if ollama_up else [])
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: providers.OllamaProvider("qwen3:8b") if kind == "ollama" else providers.OmniRouterProvider("auto/best-free"))
    monkeypatch.setattr(cli, "_cloudflare_entry", lambda: None)
    monkeypatch.setattr(cli, "_deepseek_entry", lambda: None)
    monkeypatch.setattr(cli, "_router_entry_if_configured", lambda: None)
    if prefer is None:
        monkeypatch.delenv("UH_ROUTER_PREFER", raising=False)
    else:
        monkeypatch.setenv("UH_ROUTER_PREFER", prefer)
    chain, _model = cli._build_chain(None, None, preferred="omnirouter")
    return [kind for kind, _provider in chain]


def test_router_prefers_local_when_ollama_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured cloud default must not outrank a running local model."""
    assert _chain_kinds(monkeypatch, ollama_up=True)[0] == "ollama"


def test_router_falls_back_to_cloud_when_ollama_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _chain_kinds(monkeypatch, ollama_up=False)[0] == "omnirouter"


def test_router_respects_uh_router_prefer_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = _chain_kinds(monkeypatch, ollama_up=True, prefer="cloud")
    assert kinds[0] == "omnirouter"
    assert "ollama" in kinds, "cloud first is a preference, not a removal"


def test_router_prefer_local_keeps_the_cloud_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = _chain_kinds(monkeypatch, ollama_up=True, prefer="local")
    assert kinds[0] == "ollama"
    assert "omnirouter" in kinds, "a local failure must still fall back"


def test_a_local_failure_falls_back_without_failing_the_pass(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama answers, then the chat times out: the pass must still be ok."""
    from uh.dream_pass import DreamConfig, DreamScheduler

    class FlakyLocal:
        def __init__(self):
            self.calls = 0
            self._usage = {"prompt": 0, "completion": 0, "total": 0}

        def usage_snapshot(self):
            return dict(self._usage)

        def chat(self, messages, tools):
            self.calls += 1
            raise TimeoutError("local model timed out")

    class Cloud:
        def __init__(self):
            self._usage = {"prompt": 10, "completion": 5, "total": 15}

        def usage_snapshot(self):
            return dict(self._usage)

        def chat(self, messages, tools):
            return {"content": '{"duplicates": [], "orphans": [], "inconsistencies": [], "ideas": []}'}

    engine = DreamScheduler(
        config=DreamConfig(output_dir=tmp_path, sources=("siyuan",)),
        provider=FlakyLocal(),
        uhb_url="http://127.0.0.1:8101",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
            200, json={"complexity": "standard", "blocks": [{"type": "p", "content": "x"}]}
        ))),
    )

    report = engine.run_once()

    assert report.status == "ok", "a local failure is a note, not a failed pass"
    assert any("model call failed" in note for note in report.notes)
