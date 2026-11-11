"""uh memory migrate/show: idempotent, proven on a file the toolset really wrote."""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import cli
from uh import config as uh_config
from uh.store import Store
from uh.toolsets.memory import MemoryToolset, memory_path


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    for name in uh_config.ENV_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return home


def _produce_real_memory_file(home: Path) -> Path:
    """Write through the toolset, exactly as the agent would - no hand-made fixture."""
    toolset = MemoryToolset()  # UH_HOME is patched: writes $UH_HOME/MEMORY.md
    assert toolset.call("memory_write", {"section": "Notes", "content": "- le chat aime le clavier"})[
        "path"
    ]
    toolset.call("memory_write", {"section": "Preferences", "content": "- reponses en francais"})
    path = memory_path()
    assert path.is_file()
    assert "clavier" in path.read_text(encoding="utf-8")
    return path


def _backups(home: Path) -> list[Path]:
    return sorted(home.glob("MEMORY.md.bak.*"))


def test_migrate_requires_a_real_file(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["memory", "migrate"])
    captured = capsys.readouterr()
    assert code == 1
    assert "no memory file at" in captured.err
    assert not (home / "uh.db").exists() or not Store(home / "uh.db").list_sessions()


def test_migrate_twice_is_idempotent(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _produce_real_memory_file(home)
    original = source.read_text(encoding="utf-8")

    # first run
    assert cli.main(["memory", "migrate"]) == 0
    first = capsys.readouterr()
    assert "migrated" in first.out
    assert _backups(home), "a backup must exist after migration"

    with Store(home / "uh.db") as store:
        session = store.find_session_by_metadata("kind", "memory")
        assert session is not None
        assert session["title"] == "memory"
        messages = store.get_messages(session["id"])
        assert len(store.list_sessions()) == 1

    assert len(messages) == 2  # Notes + Preferences, Learned was empty
    assert messages[0]["content"].startswith("[memory:Notes]")
    assert "clavier" in messages[0]["content"]
    assert messages[1]["content"].startswith("[memory:Preferences]")
    assert source.read_text(encoding="utf-8") == original, "MEMORY.md must be untouched"
    backups_after_first = len(_backups(home))

    # second run: no new session, no new backup, no new message
    assert cli.main(["memory", "migrate"]) == 0
    second = capsys.readouterr()
    assert "already migrated" in second.err

    with Store(home / "uh.db") as store:
        assert len(store.list_sessions()) == 1
        session = store.find_session_by_metadata("kind", "memory")
        assert store.message_count(session["id"]) == 2

    assert len(_backups(home)) == backups_after_first == 1


def test_memory_show_is_read_only(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _produce_real_memory_file(home)
    before = source.read_text(encoding="utf-8")

    assert cli.main(["memory", "show"]) == 0
    out = capsys.readouterr().out
    assert "## Notes" in out
    assert "clavier" in out
    assert source.read_text(encoding="utf-8") == before


def test_memory_show_without_file(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["memory", "show"]) == 1
    assert "no memory file at" in capsys.readouterr().err
