"""Provider keys: storage, permissions, no leak, provider build, CLI wiring."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import cli
from uh import keys as keys_mod
from uh.providers import ProviderError, build_provider
from uh.providers.openai_compat import OpenAICompatProvider

# Not a secret and deliberately not shaped like one: the repository must stay
# clean for a grep of provider key patterns (sk-..., ghp_..., AIza...).
SECRET = "test-key-not-a-real-secret"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("UH_HOME", str(path))
    for name in ("UH_PROVIDER_KEY", "UH_OMNIROUTER_URL", "OMNIROUTE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return path


def test_add_read_remove_roundtrip(home: Path) -> None:
    path = keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "llama-3.1-8b", SECRET)
    assert path == home / "keys.toml"
    assert path.is_file()

    entry = keys_mod.get_provider("groq")
    assert entry == {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.1-8b",
        "key": SECRET,
        "keys": [SECRET],
    }
    assert keys_mod.has_key("groq")
    assert keys_mod.provider_names() == ["groq"]

    assert keys_mod.remove_provider("groq") is True
    assert keys_mod.get_provider("groq") is None
    assert keys_mod.remove_provider("groq") is False


def test_permissions_are_restricted(home: Path) -> None:
    path = keys_mod.add_provider("groq", "https://x.test/v1", "m", SECRET)
    if os.name == "nt":
        pytest.skip("POSIX mode bits are not meaningful on Windows")
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_invalid_inputs_are_refused(home: Path) -> None:
    with pytest.raises(ValueError):
        keys_mod.add_provider("Bad Name", "https://x.test/v1", "m", SECRET)
    with pytest.raises(ValueError):
        keys_mod.add_provider("groq", "ftp://x.test", "m", SECRET)


def test_build_provider_uses_a_configured_gateway(home: Path) -> None:
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "llama-3.1-8b", SECRET)

    provider = build_provider("groq", "")
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "https://api.groq.com/openai/v1"
    assert provider.model == "llama-3.1-8b"
    assert provider.api_key == SECRET

    explicit = build_provider("groq", "autre-modele")
    assert explicit.model == "autre-modele"


def test_unknown_provider_lists_configured_kinds(home: Path) -> None:
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    with pytest.raises(ProviderError) as excinfo:
        build_provider("nope", "")
    message = str(excinfo.value)
    assert "unknown provider" in message
    assert "groq" in message and "ollama" in message


def test_providers_list_never_prints_the_key(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)

    assert cli.main(["providers", "list"]) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert "key present" in captured.out


def test_cli_add_and_remove(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # No --key option any more: argv is visible in the process list and kept in
    # the shell history. The key comes from the environment, stdin or a prompt.
    monkeypatch.setenv("UH_PROVIDER_KEY", SECRET)
    code = cli.main(
        [
            "providers",
            "add",
            "groq",
            "--base-url",
            "https://api.groq.com/openai/v1",
            "--model",
            "llama-3.1-8b",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "added provider groq" in captured.out
    assert SECRET not in captured.out and SECRET not in captured.err
    assert keys_mod.get_provider("groq")["key"] == SECRET

    assert cli.main(["providers", "remove", "groq"]) == 0
    assert keys_mod.get_provider("groq") is None
    assert cli.main(["providers", "remove", "groq"]) == 1


def test_cli_refuses_a_key_in_argv(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`uh providers add --key ...` must fail: argparse rejects the option."""
    with pytest.raises(SystemExit) as failure:
        cli.main(["providers", "add", "groq", "--key", SECRET])
    assert failure.value.code == 2
    assert keys_mod.get_provider("groq") is None


def test_cli_add_requires_fields(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["providers", "add", "groq"]) == 1
    assert "--base-url and --model are required" in capsys.readouterr().err


def test_doctor_reports_key_presence_only(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["doctor"])
    assert "keys" in capsys.readouterr().out

    keys_mod.add_provider("groq", "https://api.groq.com/openai/v1", "m", SECRET)
    cli.main(["doctor"])
    captured = capsys.readouterr()
    assert SECRET not in captured.out
    assert "present" in captured.out
