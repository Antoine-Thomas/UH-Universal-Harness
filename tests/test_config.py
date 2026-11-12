"""Layered configuration: precedence, unknown keys, writer round-trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import cli
from uh import config as uh_config
from uh._compat import tomllib


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    for env_name in uh_config.ENV_KEYS.values():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.chdir(tmp_path)
    return home


def test_defaults_only(isolated: Path) -> None:
    cfg = uh_config.load()
    assert cfg.get("default_provider") == "ollama"
    assert cfg.get("max_iterations") == 8
    assert cfg.get("stream") is True
    assert cfg.source("max_iterations") == "default"
    assert cfg.resolved_store_path() == (isolated / "uh.db").resolve()


def test_user_then_project_then_env(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (isolated / "config.toml").write_text('default_model = "user-model"\n', encoding="utf-8")
    assert uh_config.load().get("default_model") == "user-model"

    (Path.cwd() / "uh.toml").write_text('default_model = "project-model"\n', encoding="utf-8")
    cfg = uh_config.load()
    assert cfg.get("default_model") == "project-model"
    assert cfg.source("default_model") == "project"

    monkeypatch.setenv("UH_DEFAULT_MODEL", "env-model")
    cfg = uh_config.load()
    assert cfg.get("default_model") == "env-model"
    assert cfg.source("default_model") == "env"

    cli_cfg = uh_config.load({"default_model": "flag-model"})
    assert cli_cfg.get("default_model") == "flag-model"
    assert cli_cfg.source("default_model") == "cli"


def test_unknown_key_warns_and_is_ignored(isolated: Path) -> None:
    (isolated / "config.toml").write_text(
        'default_model = "ok"\nfuture_key = "ignored"\n', encoding="utf-8"
    )
    cfg = uh_config.load()
    assert cfg.get("default_model") == "ok"
    assert any("future_key" in warning for warning in cfg.warnings)


def test_wrong_type_in_file_warns(isolated: Path) -> None:
    (isolated / "config.toml").write_text('max_iterations = "beaucoup"\n', encoding="utf-8")
    cfg = uh_config.load()
    assert cfg.get("max_iterations") == 8
    assert cfg.source("max_iterations") == "default"
    assert any("max_iterations" in warning for warning in cfg.warnings)


def test_write_layer_preserves_other_keys(isolated: Path) -> None:
    uh_config.write_layer("user", "default_model", "premier")
    uh_config.write_layer("user", "max_iterations", "12")

    path = isolated / "config.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data == {"default_model": "premier", "max_iterations": 12}
    assert path.read_text(encoding="utf-8").startswith("# UH configuration")

    cfg = uh_config.load()
    assert cfg.get("max_iterations") == 12
    assert cfg.source("max_iterations") == "user"


def test_write_layer_coerces_types(isolated: Path) -> None:
    uh_config.write_layer("user", "stream", "false")
    uh_config.write_layer("user", "banner", "0")
    data = tomllib.loads((isolated / "config.toml").read_text(encoding="utf-8"))
    assert data == {"stream": False, "banner": False}


@pytest.mark.parametrize("value", ["beaucoup", "1.5", ""])
def test_write_layer_rejects_bad_int(isolated: Path, value: str) -> None:
    with pytest.raises(uh_config.ConfigError):
        uh_config.write_layer("user", "max_iterations", value)


def test_write_layer_rejects_unknown_key(isolated: Path) -> None:
    with pytest.raises(uh_config.ConfigError):
        uh_config.write_layer("user", "not_a_key", "1")


def test_toml_writer_escapes(isolated: Path) -> None:
    awkward = 'a "quoted" \\ path\nsecond\tline'
    uh_config.write_layer("user", "workspace", awkward)
    data = tomllib.loads((isolated / "config.toml").read_text(encoding="utf-8"))
    assert data["workspace"] == awkward


def test_writer_rejects_non_flat_values() -> None:
    with pytest.raises(uh_config.ConfigError):
        uh_config.dump_flat_toml({"x": [1, 2]})


def test_cli_show_get_set_path(isolated: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["config", "set", "default_model", "abc", "--user"]) == 0
    assert cli.main(["config", "get", "default_model"]) == 0
    assert capsys.readouterr().out.strip() == "abc"

    assert cli.main(["config", "show"]) == 0
    shown = capsys.readouterr().out
    assert "default_model" in shown and "user" in shown

    assert cli.main(["config", "path"]) == 0
    assert "config.toml" in capsys.readouterr().out

    assert cli.main(["config", "get", "nope"]) == 1
    assert cli.main(["config", "set", "nope", "1"]) == 1


def test_project_layer_wins_over_user_via_cli(
    isolated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["config", "set", "default_model", "depuis-user", "--user"])
    cli.main(["config", "set", "default_model", "depuis-projet", "--project"])
    capsys.readouterr()

    cli.main(["config", "get", "default_model"])
    assert capsys.readouterr().out.strip() == "depuis-projet"
