"""``--provider X`` without ``--model``: X owns the model choice.

Measured 2026-11-17: ``uh chat --provider omnirouter "PING"`` inherited the
global ``default_model`` (``deepseek-flash``), a DeepSeek id OmniRoute does not
know. OmniRoute answered HTTP 401 ``provider_not_configured`` and UH printed
"clé refusée", which sent a whole diagnosis after a key that was in fact valid.

The rule these tests pin: an explicit ``--model`` wins; otherwise the requested
provider's own default applies — ``omnirouter_model`` for the gateway, the first
local tag for Ollama, the provider's own ``DEFAULT_MODEL`` for the rest. The
global ``default_model`` belongs to the default chain, never to a ``--provider``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import cli
from uh import config as uh_config
from uh.providers import ProviderError
from uh.providers.cloudflare import DEFAULT_MODEL as CLOUDFLARE_DEFAULT_MODEL
from uh.providers.deepseek import DEFAULT_MODEL as DEEPSEEK_DEFAULT_MODEL
from uh.providers.omnirouter import DEFAULT_MODEL as OMNIROUTER_DEFAULT_MODEL

# Every source of ambient credentials/defaults must be off: the user's machine
# has a real OmniRoute and real keys, and a test that reads them proves nothing.
ISOLATED_ENV = (
    "UH_CLOUDFLARE_ACCOUNT_ID",
    "UH_CLOUDFLARE_API_TOKEN",
    "UH_DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY",
    "UH_OMNIROUTER_URL",
    "OMNIROUTE_BASE_URL",
    "UH_OMNIROUTER_API_KEY",
    "OMNIROUTE_API_KEY",
    "UH_DEFAULT_MODEL",
    "UH_DEFAULT_PROVIDER",
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    for name in ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)
    # A gateway URL and a throwaway key: the provider constructors need one,
    # and no test here touches the network.
    monkeypatch.setenv("UH_OMNIROUTER_URL", "http://127.0.0.1:20128")
    monkeypatch.setenv("UH_OMNIROUTER_API_KEY", "test-omnirouter-key-0123456789")
    monkeypatch.setattr(cli, "list_models", lambda: [])
    return home


def test_requested_omnirouter_uses_the_configured_omnirouter_model() -> None:
    chain, model = cli._build_chain("omnirouter", None, "auto/best-free")

    assert [kind for kind, _ in chain] == ["omnirouter"]
    assert model == "auto/best-free"
    assert chain[0][1].model == "auto/best-free"


def test_requested_omnirouter_falls_back_to_the_provider_default() -> None:
    chain, model = cli._build_chain("omnirouter", None, "")

    assert model == OMNIROUTER_DEFAULT_MODEL
    assert chain[0][1].model == OMNIROUTER_DEFAULT_MODEL


def test_an_explicit_model_still_wins() -> None:
    chain, model = cli._build_chain("omnirouter", "auto/best-coding", "auto/best-free")

    assert model == "auto/best-coding"
    assert chain[0][1].model == "auto/best-coding"


def test_requested_deepseek_keeps_its_own_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_DEEPSEEK_API_KEY", "test-deepseek-key-0123456789")

    chain, model = cli._build_chain("deepseek", None)

    assert chain[0][1].model == DEEPSEEK_DEFAULT_MODEL
    assert model == DEEPSEEK_DEFAULT_MODEL


def test_requested_cloudflare_keeps_its_own_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_CLOUDFLARE_ACCOUNT_ID", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("UH_CLOUDFLARE_API_TOKEN", "cf-test-token-0123456789")

    chain, _model = cli._build_chain("cloudflare", None)

    assert chain[0][1].model == CLOUDFLARE_DEFAULT_MODEL


def test_requested_ollama_takes_the_first_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli, "list_models", lambda: [{"name": "llama3:8b", "size": 1, "modified_at": ""}]
    )

    chain, model = cli._build_chain("ollama", None)

    assert model == "llama3:8b"
    assert chain[0][1].model == "llama3:8b"


def test_requested_ollama_with_no_local_model_asks_for_one() -> None:
    with pytest.raises(ProviderError) as failure:
        cli._build_chain("ollama", None)

    assert "model name is required" in str(failure.value)


def test_chat_session_never_hands_the_global_default_to_another_provider(
    tmp_path: Path,
) -> None:
    """The measured bug, through the entry point the user actually runs."""
    cfg = uh_config.load({"default_model": "deepseek-flash"})

    session, code = cli._build_chat_session(
        cli._chat_namespace(
            provider="omnirouter",
            once=True,
            no_stream=True,
            no_save=True,
            max_iterations=1,
            prompt="PING",
            workspace=str(tmp_path),
        ),
        cfg,
        announce=False,
    )

    assert code == 0
    assert session is not None
    try:
        assert session.provider.model == "auto/best-free"
    finally:
        session.close()
