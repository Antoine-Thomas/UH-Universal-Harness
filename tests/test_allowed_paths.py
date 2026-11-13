"""The write whitelist: ~/Desktop, ~/Documents, ~/Downloads, ~/uh-workspace.

The file sandbox refuses writes outside the workspace - that stays true for
every directory nobody declared. What changes is that the four home directories
(plus whatever ``UH_ALLOWED_PATHS`` or a hand-written ``[providers.file]`` adds)
are writable without moving the workspace, and that a system directory is never
writable whatever the list says.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from uh import config as uh_config
from uh import sandbox
from uh.toolsets.file import FileToolset

SYSTEM_TARGET = "C:/Windows/Temp" if os.name == "nt" else "/etc"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake $HOME with the four directories, so no test touches the real one."""
    root = tmp_path / "home"
    for name in sandbox.ALLOWED_HOME_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: root))
    # ~ must resolve inside the fake home too: Path.expanduser() goes through
    # os.path.expanduser, which reads HOME/USERPROFILE and ignores Path.home().
    monkeypatch.setattr(
        os.path, "expanduser", lambda value: str(root) + value[1:] if value.startswith("~") else value
    )
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "projet"
    target.mkdir()
    return target


@pytest.fixture
def toolset(workspace: Path, home: Path) -> FileToolset:
    return FileToolset(workspace)


# --------------------------------------------------------------------------- #
# building the list
# --------------------------------------------------------------------------- #
def test_the_defaults_are_the_workspace_and_the_four_home_directories(
    workspace: Path, home: Path
) -> None:
    roots = sandbox.allowed_paths(workspace)

    assert roots[0] == workspace.resolve(), "the workspace comes first"
    for name in ("Desktop", "Documents", "Downloads", "uh-workspace"):
        assert (home / name) in roots


def test_uh_allowed_paths_adds_directories(
    workspace: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extra = tmp_path / "partage"
    extra.mkdir()
    second = tmp_path / "archives"
    monkeypatch.setenv("UH_ALLOWED_PATHS", os.pathsep.join([str(extra), str(second)]))

    roots = sandbox.allowed_paths(workspace)

    assert extra.resolve() in roots
    assert second.resolve() in roots, "a directory need not exist to be listed"


def test_a_hand_written_config_table_is_read(
    workspace: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "equipe"
    shared.mkdir()
    uh_home = tmp_path / "uh-home"
    uh_home.mkdir(exist_ok=True)  # conftest already created $UH_HOME
    monkeypatch.setenv("UH_HOME", str(uh_home))
    (uh_home / "config.toml").write_text(
        '[providers.file]\nallowed_paths = ["' + str(shared).replace("\\", "/") + '"]\n',
        encoding="utf-8",
    )

    config = uh_config.load()

    assert config.file_allowed_paths() == [shared]
    assert not [w for w in config.warnings if "providers" in w], "a known table must not warn"
    assert shared.resolve() in sandbox.allowed_paths(workspace)


def test_a_single_string_is_accepted(
    workspace: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uh_home = tmp_path / "uh-home"
    uh_home.mkdir(exist_ok=True)  # conftest already created $UH_HOME
    monkeypatch.setenv("UH_HOME", str(uh_home))
    (uh_home / "config.toml").write_text(
        '[providers.file]\nallowed_paths = "' + str(tmp_path / "un").replace("\\", "/") + '"\n',
        encoding="utf-8",
    )

    assert uh_config.load().file_allowed_paths() == [tmp_path / "un"]


def test_the_flat_writer_refuses_to_drop_a_table(
    workspace: Path, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Better a refusal than silently deleting a hand-written table."""
    uh_home = tmp_path / "uh-home"
    uh_home.mkdir(exist_ok=True)  # conftest already created $UH_HOME
    monkeypatch.setenv("UH_HOME", str(uh_home))
    (uh_home / "config.toml").write_text("[providers.file]\nallowed_paths = []\n", encoding="utf-8")

    with pytest.raises(uh_config.ConfigError) as caught:
        uh_config.write_layer("user", "banner", False)

    assert "nested table" in str(caught.value)
    assert "UH_ALLOWED_PATHS" in str(caught.value)
    assert "[providers.file]" in (uh_home / "config.toml").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["Desktop", "Documents", "Downloads", "uh-workspace"])
def test_write_file_accepts_a_whitelisted_directory(
    toolset: FileToolset, home: Path, name: str
) -> None:
    target = home / name / "note.txt"

    result = toolset.write_file(str(target), "bonjour")

    assert result.get("error") is None
    assert target.read_text(encoding="utf-8") == "bonjour"
    assert result["created"] is True


def test_patch_accepts_a_whitelisted_file(toolset: FileToolset, home: Path) -> None:
    target = home / "Documents" / "note.txt"
    target.write_text("v1", encoding="utf-8")

    result = toolset.patch(str(target), "v1", "v2")

    assert result.get("error") is None
    assert target.read_text(encoding="utf-8") == "v2"


def test_a_write_outside_both_lists_is_refused_with_the_list(
    toolset: FileToolset, tmp_path: Path, home: Path
) -> None:
    target = tmp_path / "ailleurs" / "note.txt"

    result = toolset.write_file(str(target), "non")

    assert not target.exists()
    message = result["error"]
    assert "refused:" in message
    assert "outside the workspace and outside the allowed paths" in message
    assert "~/Desktop" in message and "~/uh-workspace" in message
    assert "Use /workspace <path> to switch." in message
    assert "(hint: /workspace <path> or `uh --workspace <path>`)" in message


def test_a_system_path_is_refused_even_when_whitelisted(
    toolset: FileToolset, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_ALLOWED_PATHS", SYSTEM_TARGET)

    assert sandbox.is_allowed(SYSTEM_TARGET + "/evil.txt", toolset.workspace) is False
    result = toolset.write_file(SYSTEM_TARGET + "/evil.txt", "non")
    assert "refused:" in result["error"]
    assert "is a system path, which UH never writes to" in result["error"]


def test_reading_stays_workspace_only(toolset: FileToolset, home: Path) -> None:
    target = home / "Desktop" / "lisible.txt"
    target.write_text("secret", encoding="utf-8")

    result = toolset.read_file(str(target))

    assert "outside the workspace" in result["error"]


def test_the_system_veto_survives_macos_symlinks() -> None:
    """macOS resolves /etc to /private/etc: both spellings must be refused."""
    assert sandbox.is_system_directory("/private/etc/hosts") is True
    assert sandbox.is_system_directory("/private/etc") is True
    assert sandbox.is_system_directory("/private/var/log") is False  # /var is not a system root


def test_is_allowed_refuses_a_private_spelling(toolset: FileToolset) -> None:
    assert sandbox.is_allowed("/private/etc/passwd", toolset.workspace) is False


def test_display_paths_uses_the_tilde(home: Path) -> None:
    rendered = sandbox.display_paths([home / "Desktop", Path("/tmp/x")])

    assert "~/Desktop" in rendered
    assert str(home) not in rendered
