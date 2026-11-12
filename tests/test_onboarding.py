"""First-run provider selection: one test per branch (no network, no download)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from uh import keys as keys_mod
from uh import onboarding
from uh.providers import ProviderError


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("UH_HOME", str(path))
    return path


def test_branch_a_uses_an_installed_model(home: Path) -> None:
    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        ollama_probe=lambda: ["qwen3:8b", "qwen2.5:0.5b"],
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )
    assert choice is not None
    assert (choice.kind, choice.model, choice.source) == ("ollama", "qwen3:8b", "ollama-existing")


def test_branch_b_pulls_the_zero_config_model_without_asking(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: True)
    asked: list[str] = []
    pulled: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    stream = io.StringIO()
    choice = onboarding.choose_provider(
        stream=stream,
        input_fn=fake_input,
        ollama_probe=lambda: [],
        pull=lambda name, cb: pulled.append(name),
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )

    assert choice is not None and choice.source == "ollama-pulled"
    assert pulled == [choice.model] == [onboarding.ZERO_CONFIG_MODEL[0]]
    assert choice.model == "qwen2.5:0.5b"
    assert asked == [], "the zero-config pull is automatic: no prompt"
    output = stream.getvalue()
    assert "downloading qwen2.5:0.5b" in output
    assert "~400 MB" in output  # the size is announced before the download


def test_branch_b_pull_failure_falls_through_to_the_router(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: True)

    def failing_pull(name: str, cb) -> None:
        raise ProviderError("boom")

    stream = io.StringIO()
    choice = onboarding.choose_provider(
        stream=stream,
        ollama_probe=lambda: [],
        pull=failing_pull,
        router_probe=lambda: True,
        keys_reader=lambda: {},
    )

    assert "pull failed: boom" in stream.getvalue()
    assert choice is not None and choice.kind == "omnirouter"


def test_branch_c_uses_a_running_router(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: False)
    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        ollama_probe=lambda: [],
        router_probe=lambda: True,
        keys_reader=lambda: {},
    )
    assert choice is not None
    assert (choice.kind, choice.source) == ("omnirouter", "omnirouter")


def test_branch_d_uses_an_already_configured_provider(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: False)
    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        ollama_probe=lambda: [],
        router_probe=lambda: False,
        keys_reader=lambda: {"groq": {"base_url": "https://api.groq.com/openai/v1", "model": "m"}},
    )
    assert choice is not None
    assert (choice.kind, choice.model, choice.source) == ("groq", "m", "user-provider")


def test_branch_e_without_a_tty_returns_none_and_prints_hints(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # not a TTY
    stream = io.StringIO()

    choice = onboarding.choose_provider(
        stream=stream,
        ollama_probe=lambda: [],
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )

    assert choice is None
    output = stream.getvalue()
    assert "openrouter.ai/keys" in output
    assert "groq" not in output  # Groq left the catalogue and the hints
    assert "not a terminal" in output


def test_branch_e_guided_setup_saves_the_key_without_printing_it(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: False)

    class FakeTty(io.StringIO):
        def isatty(self) -> bool:  # noqa: D102
            return True

    monkeypatch.setattr(sys, "stdin", FakeTty(""))
    answers = iter(["groq", "https://api.groq.com/openai/v1", "llama-3.1-8b-instant", "sk-test-SECRET"])
    stream = io.StringIO()

    choice = onboarding.choose_provider(
        stream=stream,
        input_fn=lambda prompt: next(answers),
        ollama_probe=lambda: [],
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )

    assert choice is not None
    assert (choice.kind, choice.model, choice.source) == ("groq", "llama-3.1-8b-instant", "guided")
    saved = keys_mod.get_provider("groq")
    assert saved is not None and saved["key"] == "sk-test-SECRET"
    assert "sk-test-SECRET" not in stream.getvalue(), "the key must never be echoed"
    assert keys_mod.keys_path().is_file()
