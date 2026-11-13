"""`uh uninstall`: the backup comes first, the deletion needs two flags.

The command is destructive by nature, so the tests pin what it refuses: a
`--purge` without `--yes` removes nothing, and a backup that cannot be written
stops the whole operation.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from uh import backup, cli


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "home"
    (folder / "skills").mkdir(parents=True)
    (folder / "skills" / "demo.md").write_text("# Demo\n\ncorps\n", encoding="utf-8")
    (folder / "MEMORY.md").write_text("# memoire\n", encoding="utf-8")
    monkeypatch.setenv("UH_HOME", str(folder))
    return folder


def test_without_purge_it_backs_up_and_removes_nothing(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "final.zip"

    code = cli.main(["uninstall", "--to", str(target)])
    printed = capsys.readouterr().out

    assert code == 0
    assert target.is_file()
    assert home.is_dir()
    assert "Aucune donnee n'a ete supprimee" in printed
    assert "pip uninstall uh-harness" in printed
    with zipfile.ZipFile(target) as archive:
        assert "skills/demo.md" in archive.namelist()


def test_purge_without_yes_refuses_and_keeps_the_home(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "final.zip"

    code = cli.main(["uninstall", "--to", str(target), "--purge"])
    error = capsys.readouterr().err

    assert code == cli.EXIT_USAGE
    assert home.is_dir()
    assert "--yes" in error


def test_purge_with_yes_removes_the_home_after_the_backup(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "final.zip"

    code = cli.main(["uninstall", "--to", str(target), "--purge", "--yes"])
    printed = capsys.readouterr().out

    assert code == 0
    assert not home.exists()
    assert target.is_file()
    assert "supprime" in printed


def test_a_failed_backup_stops_the_deletion(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(**_: object) -> dict:
        raise backup.BackupError("disk is full")

    monkeypatch.setattr(backup, "export", refuse)

    code = cli.main(["uninstall", "--to", str(tmp_path / "x.zip"), "--purge", "--yes"])
    error = capsys.readouterr().err

    assert code == cli.EXIT_FAILURE
    assert home.is_dir(), "nothing may be removed when the archive failed"
    assert "nothing was removed" in error


def test_the_desktop_wording_is_not_used_when_to_points_elsewhere(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cli.main(["uninstall", "--to", str(tmp_path / "ailleurs.zip")])
    printed = capsys.readouterr().out

    assert "Bureau" not in printed
    assert "sont sauvegardees" in printed
