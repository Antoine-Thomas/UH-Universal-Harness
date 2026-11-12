"""Provider registry, OmniRouter configuration discovery, fallback chain."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import cli
from uh.providers import PROVIDERS, ProviderError, build_provider, omnirouter
from uh.providers.ollama import OllamaProvider
from uh.providers.omnirouter import OmniRouterProvider


class StubProvider:
    model = "stub:1b"

    def __init__(self, answer: str = "reponse du stub") -> None:
        self.answer = answer
        self.calls = 0

    def chat(self, messages, tools, *, on_token=None):
        self.calls += 1
        if on_token is not None:
            on_token(self.answer)
        return {"role": "assistant", "content": self.answer, "tool_calls": []}


@pytest.fixture
def isolated_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No UH_OMNIROUTER_URL, no OMNIROUTE_* vars, no ~/.omniroute/.env."""
    for var in (
        "UH_OMNIROUTER_URL",
        "OMNIROUTE_BASE_URL",
        "UH_OMNIROUTER_API_KEY",
        "OMNIROUTE_API_KEY",
        "UH_OMNIROUTER_CONFIG",
    ):
        monkeypatch.delenv(var, raising=False)
    missing = tmp_path / "no.env"
    monkeypatch.setattr(omnirouter, "ENV_FILE", missing)
    return missing


def test_registry_contains_both_providers() -> None:
    assert set(PROVIDERS) == {"ollama", "omnirouter"}


def test_build_ollama_provider() -> None:
    provider = build_provider("ollama", "qwen3:8b")
    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen3:8b"


def test_build_omnirouter_without_configuration(isolated_router: Path) -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("omnirouter", "auto/best-free")
    assert "OmniRouter not configured" in str(excinfo.value)


def test_build_unknown_provider_lists_kinds(isolated_router: Path) -> None:
    with pytest.raises(ProviderError) as excinfo:
        build_provider("openai", "gpt-4o")
    message = str(excinfo.value)
    assert "unknown provider" in message
    assert "ollama" in message and "omnirouter" in message


def test_omnirouter_reads_base_url_from_environment(
    isolated_router: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_OMNIROUTER_URL", "http://127.0.0.1:20128")
    provider = build_provider("omnirouter", "auto/fast")
    assert isinstance(provider, OmniRouterProvider)
    assert provider.base_url == "http://127.0.0.1:20128"


def test_omnirouter_reads_dotenv_host_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in (
        "UH_OMNIROUTER_URL",
        "OMNIROUTE_BASE_URL",
        "UH_OMNIROUTER_API_KEY",
        "OMNIROUTE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    env_file = tmp_path / "dot.env"
    env_file.write_text("HOST=127.0.0.1\nPORT=20999\nOMNIROUTE_API_KEY=dummy\n", encoding="utf-8")
    monkeypatch.setattr(omnirouter, "ENV_FILE", env_file)

    assert omnirouter.resolve_base_url() == "http://127.0.0.1:20999"
    assert omnirouter.resolve_api_key() == "dummy"


def test_chat_falls_back_to_omnirouter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    stub = StubProvider("bonjour via omnirouter")

    def fake_make_provider(kind: str, model: str):
        if kind == "ollama":
            raise ProviderError("cannot reach Ollama at http://127.0.0.1:11434")
        return stub

    monkeypatch.setattr(cli, "_make_provider", fake_make_provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("dis bonjour\n"))
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))

    code = cli.main(
        ["chat", "--once", "--model", "stub:1b", "--workspace", str(tmp_path), "--no-stream"]
    )

    out = capsys.readouterr()
    assert code == 0
    assert "bonjour via omnirouter" in out.out
    assert "fallback: ollama -> omnirouter" in out.err


def test_explicit_provider_has_no_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def fake_make_provider(kind: str, model: str):
        raise ProviderError(f"{kind} is down")

    monkeypatch.setattr(cli, "_make_provider", fake_make_provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("bonjour\n"))

    code = cli.main(["chat", "--once", "--provider", "ollama", "--model", "x", "--workspace", str(tmp_path)])

    out = capsys.readouterr()
    assert code == 1
    assert "ollama is down" in out.err
    assert "fallback:" not in out.err


def test_providers_list_without_omnirouter_configuration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], isolated_router: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "list_models",
        lambda: [{"name": "qwen3:8b", "size": 1, "modified_at": ""}],
    )

    code = cli.main(["providers", "list"])

    out = capsys.readouterr()
    assert code == 0
    assert "ollama" in out.out
    assert "qwen3:8b" in out.out
    assert "not configured" in out.out
    assert "registered: ollama, omnirouter" in out.out
