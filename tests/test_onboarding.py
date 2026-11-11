"""First-run provider selection: one test per branch (no network, no download)."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from uh import keys as keys_mod
from uh import onboarding


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("UH_HOME", str(path))
    return path


def test_ram_detection_is_plausible() -> None:
    ram = onboarding.detect_ram_gb()
    assert ram is None or (isinstance(ram, float) and ram > 0)


def test_recommended_model_scales_and_never_goes_below_1_5b() -> None:
    assert onboarding.recommended_model(4.0)[0] == "qwen2.5:1.5b"
    assert onboarding.recommended_model(8.0)[0] == "qwen2.5:3b"
    assert onboarding.recommended_model(32.0)[0] == "qwen2.5:7b"
    assert onboarding.recommended_model(None)[0] == "qwen2.5:3b"  # unknown RAM -> 8 GB
    assert all(minimum >= 4 for _, _, minimum in onboarding.SMALL_MODELS)
    assert all(not name.endswith(":0.5b") for name, _, _ in onboarding.SMALL_MODELS)


def test_branch_a_uses_an_installed_model(home: Path) -> None:
    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        ollama_probe=lambda: ["qwen3:8b", "qwen2.5:0.5b"],
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )
    assert choice is not None
    assert (choice.kind, choice.model, choice.source) == ("ollama", "qwen3:8b", "ollama-existing")


def test_branch_b_offers_a_sized_model_and_pulls_only_after_confirmation(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: True)
    asked: list[str] = []
    pulled: list[str] = []

    def fake_input(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        input_fn=fake_input,
        ollama_probe=lambda: [],
        ram_detector=lambda: 8.0,
        pull=lambda name, cb: pulled.append(name),
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )

    assert choice is not None and choice.source == "ollama-pulled"
    assert pulled == [choice.model] == ["qwen2.5:3b"]
    assert asked and "~2.0 GB" in asked[0], asked  # size shown before downloading
    assert "1.5b" not in choice.model or True  # floor is 1.5b, never 0.5b
    assert ":0.5b" not in choice.model


def test_branch_b_refusal_falls_through_to_the_router(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: True)
    pulled: list[str] = []

    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        input_fn=lambda prompt: "n",
        ollama_probe=lambda: [],
        ram_detector=lambda: 8.0,
        pull=lambda name, cb: pulled.append(name),
        router_probe=lambda: True,
        keys_reader=lambda: {},
    )

    assert pulled == []  # nothing downloaded without a yes
    assert choice is not None and choice.kind == "omnirouter"


def test_branch_c_uses_a_running_router(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding, "_ollama_is_up", lambda: False)
    choice = onboarding.choose_provider(
        stream=io.StringIO(),
        ollama_probe=lambda: [],
        ram_detector=lambda: 8.0,
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
        ram_detector=lambda: 8.0,
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
        ram_detector=lambda: 8.0,
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )

    assert choice is None
    output = stream.getvalue()
    assert "console.groq.com/keys" in output
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
        ram_detector=lambda: 8.0,
        router_probe=lambda: False,
        keys_reader=lambda: {},
    )

    assert choice is not None
    assert (choice.kind, choice.model, choice.source) == ("groq", "llama-3.1-8b-instant", "guided")
    saved = keys_mod.get_provider("groq")
    assert saved is not None and saved["key"] == "sk-test-SECRET"
    assert "sk-test-SECRET" not in stream.getvalue(), "the key must never be echoed"
    assert keys_mod.keys_path().is_file()
