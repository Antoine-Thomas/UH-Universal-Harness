"""G12 — `voice.toml` ne porte aucun secret et les outils de l'agent ne l'écrivent pas.

La politique de voix cloud est un fichier de trois mots dans ``$UH_HOME``. Deux
risques à couvrir : qu'un secret y atterrisse, et qu'un outil de l'agent (mode
``dev`` ou non) puisse le réécrire derrière l'utilisateur.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh.audio import policy
from uh.toolsets import build_toolsets


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    folder = tmp_path / "home"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_the_policy_file_holds_three_words_and_no_secret(home: Path) -> None:
    path = policy.save_policy("ask")

    text = path.read_text(encoding="utf-8").strip()
    assert text == 'cloud = "ask"'
    lowered = text.lower()
    for forbidden in ("key", "token", "secret", "sk-", "mistral_api", "password"):
        assert forbidden not in lowered, f"{forbidden!r} ne doit jamais entrer dans {path.name}"


def test_the_agent_tools_cannot_write_it(home: Path, tmp_path: Path) -> None:
    """`write_file` et `patch` restent dans l'espace de travail : `$UH_HOME` est dehors."""
    target = home / policy.POLICY_FILE
    policy.save_policy("never")
    before = target.read_bytes()

    catalog = build_toolsets(workspace=tmp_path / "ws")
    result = catalog["file"].write_file(str(target), 'cloud = "always"\n')

    # Les outils rendent un refus (`{"error": ...}`), ils n'écrivent pas : le fichier
    # ne bouge pas et le message dit pourquoi.
    assert isinstance(result, dict) and "error" in result, result
    assert "UH configuration folder" in result["error"]
    assert target.read_bytes() == before, "le fichier ne doit pas avoir bougé"


def test_reading_it_is_refused_too(home: Path) -> None:
    policy.save_policy("never")
    catalog = build_toolsets(workspace=Path.cwd())

    result = catalog["file"].read_file(str(home / policy.POLICY_FILE))

    assert isinstance(result, dict) and "error" in result, result
    assert "UH configuration folder" in result["error"]


def test_patch_does_not_touch_it_either(home: Path, tmp_path: Path) -> None:
    policy.save_policy("ask")
    target = home / policy.POLICY_FILE
    before = target.read_bytes()

    result = build_toolsets(workspace=tmp_path / "ws")["file"].patch(
        str(target), 'cloud = "ask"', 'cloud = "always"'
    )

    assert "error" in result, result
    assert "UH configuration folder" in result["error"]
    assert target.read_bytes() == before


def test_the_terminal_neither_reads_nor_writes_it(home: Path, tmp_path: Path) -> None:
    """Les clés vivent dans `$UH_HOME` : le terminal ne doit pas les sortir."""
    target = home / policy.POLICY_FILE
    policy.save_policy("never")
    before = target.read_bytes()
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    tools = build_toolsets(workspace=workspace)["terminal"]
    posix = str(target).replace("\\", "/")

    read = tools.terminal(f"cat {posix}")
    write = tools.terminal(f'echo cloud = "always" > {posix}')
    via_tilde = tools.terminal("cat ~/.uh/voice.toml")

    for result in (read, write, via_tilde):
        assert result.get("refused") is True, result
        assert "UH configuration folder" in result["error"]
    assert target.read_bytes() == before, "rien ne doit avoir été écrit"


def test_the_terminal_still_runs_an_ordinary_command(home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    tools = build_toolsets(workspace=workspace)["terminal"]

    result = tools.terminal("echo bonjour")

    assert result["exit_code"] == 0
    assert "bonjour" in result["stdout"]
