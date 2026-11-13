"""Backup: one archive, and the rules that make it trustworthy.

No browser, no server: these are the file-level guarantees - what goes in, what
never goes in, what an import restores, and what protects the state it replaces.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from uh import backup
from uh.store import Store


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    store = Store(folder / "uh.db")
    session = store.create_session(model="qwen3:8b", provider="ollama")
    store.append_message(session, {"role": "user", "content": "bonjour"})
    store.close()

    (folder / "MEMORY.md").write_text("# memoire\ndecision\n", encoding="utf-8")
    (folder / "config.toml").write_text('default_model = "qwen3:8b"\n', encoding="utf-8")
    passes = folder / "dreams" / "passes" / "20261113-030000"
    passes.mkdir(parents=True)
    (passes / "report.md").write_text("# passe\n", encoding="utf-8")
    (folder / "skills").mkdir()
    (folder / "skills" / "demo.md").write_text("# Demo\n\n> desc\n", encoding="utf-8")
    (folder / "uploads" / "s1").mkdir(parents=True)
    (folder / "uploads" / "s1" / "note.txt").write_text("piece jointe\n", encoding="utf-8")
    (folder / "keys.json").write_text('{"deepseek": ["sk-secret"]}', encoding="utf-8")
    (folder / "backups").mkdir()
    (folder / "backups" / "old.zip").write_bytes(b"PK\x03\x04old")
    return folder


def names(archive: Path) -> set:
    with zipfile.ZipFile(archive) as zipped:
        return set(zipped.namelist())


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #
def test_the_archive_carries_the_data_and_nothing_else(home: Path) -> None:
    result = backup.export(home=home)

    archive = Path(result["path"])
    assert archive.is_file()
    assert archive.parent == home / "backups"
    assert archive.name.startswith("uh_backup_") and archive.name.endswith(".zip")
    entries = names(archive)
    assert {"uh.db", "MEMORY.md", "config.toml", backup.MANIFEST} <= entries
    assert any(entry.startswith("dreams/passes/") for entry in entries)
    assert any(entry.startswith("skills/") for entry in entries)
    assert any(entry.startswith("uploads/") for entry in entries)


def test_credentials_are_never_in_the_archive(home: Path) -> None:
    """A backup is a file that travels: a key inside it is a leak, not a feature."""
    result = backup.export(home=home)

    entries = names(Path(result["path"]))
    assert "keys.json" not in entries
    assert not any(entry.endswith("/keys.json") for entry in entries)
    assert "keys.json" in result["excludes"]


def test_an_archive_is_never_nested_in_an_archive(home: Path) -> None:
    result = backup.export(home=home)

    assert not any(entry.startswith("backups/") for entry in names(Path(result["path"])))


def test_the_manifest_says_what_it_holds_and_what_it_refused(home: Path) -> None:
    result = backup.export(home=home)

    with zipfile.ZipFile(result["path"]) as archive:
        manifest = json.loads(archive.read(backup.MANIFEST).decode("utf-8"))
    assert manifest["format"] == backup.FORMAT
    assert manifest["count"] == len(manifest["files"])
    assert "keys.json" in manifest["excludes"]
    assert manifest["excludes"]["keys.json"].startswith("credentials")


def test_the_wal_is_folded_in_before_the_database_is_copied(home: Path) -> None:
    result = backup.export(home=home)

    assert result["wal_checkpoint"] is True


def test_an_empty_home_is_not_a_backup(home: Path) -> None:
    with pytest.raises(backup.BackupError, match="does not exist"):
        backup.export(home=home / "absent")


def test_the_export_can_be_written_where_the_caller_wants(home: Path, tmp_path: Path) -> None:
    target = tmp_path / "ailleurs" / "mon-backup.zip"

    result = backup.export(destination=target, home=home)

    assert Path(result["path"]) == target
    assert target.is_file()


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #
def test_inspect_describes_the_archive(home: Path) -> None:
    result = backup.export(home=home)

    found = backup.inspect(Path(result["path"]))

    assert found["ok"] is True
    assert found["has"]["uh.db"] is True
    assert found["has"]["dreams"] is True
    assert found["rejected"] == []


def test_a_zip_without_any_uh_data_is_refused(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.zip"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("readme.txt", "not a backup")

    with pytest.raises(backup.BackupError, match="no UH data"):
        backup.inspect(bogus)


def test_a_home_without_a_database_is_still_a_backup(tmp_path: Path) -> None:
    """A fresh install with skills but no conversation yet is restorable."""
    home = tmp_path / "home"
    (home / "skills").mkdir(parents=True)
    (home / "skills" / "seul.md").write_text("# Seul\n\ncorps\n", encoding="utf-8")

    result = backup.export(home=home)
    found = backup.inspect(Path(result["path"]))

    assert found["has"]["uh.db"] is False
    assert found["has"]["skills"] is True
    assert result["wal_checkpoint"] is True, "nothing to fold in means nothing missing"


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain.zip"
    plain.write_text("just text", encoding="utf-8")

    with pytest.raises(backup.BackupError, match="not a zip"):
        backup.inspect(plain)


def test_members_that_escape_the_home_are_flagged(tmp_path: Path) -> None:
    hostile = tmp_path / "hostile.zip"
    with zipfile.ZipFile(hostile, "w") as archive:
        archive.writestr("uh.db", b"")
        archive.writestr("../outside.md", "escaped")

    found = backup.inspect(hostile)

    assert found["rejected"] == ["../outside.md"]


def test_a_name_outside_the_backup_folder_is_never_resolved(home: Path) -> None:
    backup.export(home=home)

    assert backup.resolve_backup("../uh.db", home) is None
    assert backup.resolve_backup("C:/Windows/win.ini", home) is None
    assert backup.resolve_backup("uh.db", home) is None  # not a .zip
    assert backup.resolve_backup("absent.zip", home) is None


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def test_an_import_restores_and_snapshots_first(home: Path) -> None:
    result = backup.export(home=home)
    (home / "MEMORY.md").write_text("# memoire\nMODIFIE APRES LA SAUVEGARDE\n", encoding="utf-8")

    imported = backup.import_backup(Path(result["path"]), home=home)

    assert imported["restart_required"] is True
    assert imported["restored"] >= 5
    assert (home / "MEMORY.md").read_text(encoding="utf-8") == "# memoire\ndecision\n"
    safety = Path(imported["safety"])
    assert safety.is_file() and safety.parent == home / "backups"
    with zipfile.ZipFile(safety) as archive:
        assert "MODIFIE APRES LA SAUVEGARDE" in archive.read("MEMORY.md").decode("utf-8")


def test_the_manifest_is_not_restored_into_the_home(home: Path) -> None:
    result = backup.export(home=home)

    backup.import_backup(Path(result["path"]), home=home)

    assert not (home / backup.MANIFEST).exists()


def test_an_import_never_restores_credentials(home: Path, tmp_path: Path) -> None:
    hostile = tmp_path / "with-keys.zip"
    with zipfile.ZipFile(hostile, "w") as archive:
        archive.writestr("uh.db", b"")
        archive.writestr("keys.json", '{"deepseek": ["sk-stolen"]}')

    imported = backup.import_backup(hostile, home=home)

    assert "keys.json" in imported["skipped"]
    assert not (home / "keys.json").exists() or "sk-stolen" not in (home / "keys.json").read_text(
        encoding="utf-8"
    )


def test_a_stale_wal_is_removed_before_the_database_is_replaced(home: Path) -> None:
    """A journal left next to an incoming database replays the wrong pages."""
    result = backup.export(home=home)
    (home / "uh.db-wal").write_bytes(b"stale")
    (home / "uh.db-shm").write_bytes(b"stale")

    backup.import_backup(Path(result["path"]), home=home)

    assert not (home / "uh.db-wal").exists()
    assert not (home / "uh.db-shm").exists()


def test_an_invalid_archive_restores_nothing(home: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.zip"
    with zipfile.ZipFile(bogus, "w") as archive:
        archive.writestr("readme.txt", "not a backup")
    before = sorted(path.name for path in home.iterdir())

    with pytest.raises(backup.BackupError):
        backup.import_backup(bogus, home=home)

    assert sorted(path.name for path in home.iterdir()) == before
