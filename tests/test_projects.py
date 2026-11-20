"""P3-16b, part 1: the Git state the panel shows, and the proof it is not invented.

Gate D (D-4) asks for a comparison **byte for byte** between what the cockpit displays
and what a terminal prints. That is what ``test_the_raw_status_is_the_terminal_s_output``
does, on three real states: clean, modified, and ahead of its remote.

A project that is not a repository, or a git that refuses, must come back as
``ok: False`` with a reason - a cockpit that raises on one bad folder cannot be used to
fix that folder.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from uh import projects


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ("git", "-C", str(path), *args), capture_output=True, text=True, check=False
    ).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-m", "first")
    return path


def test_the_raw_status_is_the_terminal_s_output(tmp_path: Path) -> None:
    """D-4: the string the panel would show is the string `git status` prints."""
    repo = make_repo(tmp_path / "projet")

    clean = projects.describe(repo)
    assert clean["ok"] is True and clean["branch"] == "main" and clean["dirty"] == 0
    assert clean["porcelain"] == git(repo, "status", "--porcelain=v1", "-b")

    (repo / "note.txt").write_text("x\n", encoding="utf-8")
    dirty = projects.describe(repo)
    assert dirty["dirty"] == 1
    assert dirty["porcelain"] == git(repo, "status", "--porcelain=v1", "-b")
    assert "note.txt" in dirty["porcelain"]


def test_ahead_and_behind_come_from_the_header_line(tmp_path: Path) -> None:
    upstream = make_repo(tmp_path / "amont")
    clone = tmp_path / "clone"
    subprocess.run(
        ("git", "clone", str(upstream), str(clone)), capture_output=True, text=True, check=False
    )
    git(clone, "config", "user.email", "test@example.com")
    git(clone, "config", "user.name", "Test")
    (clone / "deux.txt").write_text("2\n", encoding="utf-8")
    git(clone, "add", "-A")
    git(clone, "commit", "-m", "second")

    facts = projects.describe(clone)

    assert facts["ahead"] == 1 and facts["behind"] == 0, facts["porcelain"]
    assert facts["porcelain"] == git(clone, "status", "--porcelain=v1", "-b")


def test_a_folder_that_is_not_a_repository_is_reported_not_raised(tmp_path: Path) -> None:
    plain = tmp_path / "pas-un-depot"
    plain.mkdir()

    facts = projects.describe(plain)

    assert facts["ok"] is False and "dépôt" in facts["reason"]
    assert facts["porcelain"] == "" and facts["dirty"] == 0


def test_a_missing_path_is_reported_not_raised(tmp_path: Path) -> None:
    facts = projects.describe(tmp_path / "inexistant")
    assert facts["ok"] is False and facts["reason"]


def test_parse_porcelain_reads_a_detached_header_without_inventing_a_branch() -> None:
    facts = projects.parse_porcelain("## HEAD (no branch)\n M a.py\n?? b.py\n")
    assert facts["branch"] == "HEAD (no branch)"
    assert facts["dirty"] == 2
    assert (facts["ahead"], facts["behind"]) == (0, 0)


def test_the_registry_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    repo = make_repo(tmp_path / "projet")

    entry = projects.add(repo, label="Mon projet")
    rows = projects.load_registry()

    assert entry["path"] == str(repo)
    assert [row["path"] for row in rows] == [str(repo)]
    assert projects.registry_path().is_file()


def test_adding_a_non_repository_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    plain = tmp_path / "fichier"
    plain.mkdir()

    with pytest.raises(projects.ProjectsError):
        projects.add(plain)


def test_switching_requires_a_known_project_when_a_root_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    fence = tmp_path / "atelier"
    inside = make_repo(fence / "connu")
    outside = make_repo(tmp_path / "ailleurs")
    monkeypatch.setattr(projects, "allowed_root", lambda: fence)

    assert projects.switch(inside) == inside
    with pytest.raises(projects.ProjectsError):
        projects.switch(outside)


def test_listing_puts_the_current_project_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    courant = make_repo(tmp_path / "courant")
    autre = make_repo(tmp_path / "autre")
    projects.add(autre)

    data = projects.listing(courant)

    assert data["projects"][0]["path"] == str(courant)
    assert data["projects"][0]["current"] is True
    assert any(item["path"] == str(autre) for item in data["projects"])
