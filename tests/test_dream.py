"""The dream journal and snapshots: traced writes, and a way back.

An autonomous pass is only acceptable if its writes are recorded and reversible.
These tests describe that contract, including the parts that refuse: a snapshot
that points outside the trees a pass owns, a rollback without a terminal, a
``--now`` that must not run anything yet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uh import cli, dream


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A workspace with wiki/, docs/, MEMORY.md, and code that must stay out."""
    monkeypatch.setenv("UH_HOME", str(tmp_path / "uh-home"))
    root = tmp_path / "workspace"
    (root / "docs").mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / "uh").mkdir()
    (root / "tests").mkdir()
    (root / ".git").mkdir()
    (root / "docs" / "guide.md").write_text("# guide\nla version 1", encoding="utf-8")
    (root / "wiki" / "concepts" / "rag.md").write_text("# RAG\nhybride", encoding="utf-8")
    (root / "MEMORY.md").write_text("# Memory\n\n## Facts\nun fait", encoding="utf-8")
    (root / "uh" / "secret.py").write_text("SECRET = 1", encoding="utf-8")
    (root / "tests" / "test_x.py").write_text("def test_x(): pass", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x'", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# journal
# --------------------------------------------------------------------------- #
def test_dream_journal_append_read(tmp_path: Path) -> None:
    journal = dream.DreamJournal(tmp_path / "dreams")
    for index in range(3):
        journal.append(
            {
                "type": "write",
                "path": f"docs/note{index}.md",
                "before_hash": None,
                "after_hash": f"h{index}",
                "bytes_delta": 10 * index,
                "reason": "compiled",
            }
        )

    entries = journal.read()

    assert len(entries) == 3
    assert {entry["path"] for entry in entries} == {
        "docs/note0.md",
        "docs/note1.md",
        "docs/note2.md",
    }
    assert all(set(entry) == {
        "timestamp", "type", "path", "before_hash", "after_hash", "bytes_delta", "reason"
    } for entry in entries)
    # One JSON object per line, not an array.
    lines = journal.file_for().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["type"] == "write"


def test_the_journal_rotates_instead_of_growing_forever(tmp_path: Path) -> None:
    journal = dream.DreamJournal(tmp_path / "dreams", max_lines=3)
    for index in range(5):
        journal.append({"type": "write", "path": f"docs/{index}.md"})

    archives = sorted((tmp_path / "dreams").glob("journal-*.1.jsonl"))

    assert len(archives) == 1, "the day was archived once"
    assert len(archives[0].read_text(encoding="utf-8").splitlines()) == 3
    assert len(journal.read()) == 2, "the new file starts empty"


def test_an_unreadable_line_does_not_kill_the_read(tmp_path: Path) -> None:
    journal = dream.DreamJournal(tmp_path / "dreams")
    journal.append({"type": "write", "path": "a"})
    with journal.file_for().open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    journal.append({"type": "write", "path": "b"})

    assert [entry["path"] for entry in journal.read()] == ["a", "b"]


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #
def test_dream_snapshot_copies_only_allowed_paths(workspace: Path) -> None:
    created = dream.create_snapshot(workspace)

    manifest = json.loads((created / "manifest.json").read_text(encoding="utf-8"))
    relatives = {entry["relative"] for entry in manifest["files"]}

    assert relatives == {"docs/guide.md", "wiki/concepts/rag.md", "MEMORY.md"}
    assert not any(name.startswith(("uh/", "tests/", ".git/")) for name in relatives)
    assert "pyproject.toml" not in relatives
    assert created.parent == dream.dreams_dir()


def test_the_snapshot_lives_in_dreams_and_git_ignores_it(workspace: Path) -> None:
    ignore = Path(__file__).resolve().parents[1] / ".gitignore"
    assert "dreams/" in ignore.read_text(encoding="utf-8").split()

    created = dream.create_snapshot(workspace)

    assert "dreams" in created.parts
    assert created.is_dir()


def test_a_snapshot_is_listed_with_its_shape(workspace: Path) -> None:
    dream.create_snapshot(workspace)

    found = dream.list_snapshots()

    assert len(found) == 1
    assert found[0]["files"] == 3
    assert found[0]["bytes"] > 0
    assert found[0]["created"]


# --------------------------------------------------------------------------- #
# rollback
# --------------------------------------------------------------------------- #
def test_dream_rollback_restores_modified_file(workspace: Path) -> None:
    dream.create_snapshot(workspace)
    (workspace / "docs" / "guide.md").write_text("# guide\nREECRIT", encoding="utf-8")

    restored = dream.rollback()

    assert restored == 3
    assert (workspace / "docs" / "guide.md").read_text(encoding="utf-8") == "# guide\nla version 1"


def test_dream_rollback_creates_backup_before_restore(workspace: Path) -> None:
    first = dream.create_snapshot(workspace)
    (workspace / "docs" / "guide.md").write_text("# guide\nREECRIT", encoding="utf-8")

    dream.rollback()

    snapshots = dream.list_snapshots()
    assert len(snapshots) == 2, "the undo of the undo must exist"
    safety = [item for item in snapshots if item["id"] != first.name][0]
    safety_tree = (dream.dreams_dir() / safety["id"] / "tree")
    contents = {path.read_text(encoding="utf-8") for path in safety_tree.iterdir()}
    assert any("REECRIT" in text for text in contents), "the pre-rollback state is kept"


def test_a_rollback_never_touches_the_code(workspace: Path) -> None:
    """A manifest that points at uh/ is corrupt: the file is skipped, not written."""
    created = dream.create_snapshot(workspace)
    manifest = json.loads((created / "manifest.json").read_text(encoding="utf-8"))
    hijack = created / "tree" / "9999"
    hijack.write_text("CODE = 'pwned'", encoding="utf-8")
    manifest["files"].append(
        {
            "original": str(workspace / "uh" / "secret.py"),
            "relative": "uh/secret.py",
            "size": 1,
            "sha256": None,
            "stored": "9999",
        }
    )
    (created / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    restored = dream.rollback()

    assert restored == 3, "the three allowed files only"
    assert (workspace / "uh" / "secret.py").read_text(encoding="utf-8") == "SECRET = 1"


def test_a_rollback_without_a_snapshot_says_so(workspace: Path) -> None:
    with pytest.raises(dream.DreamError) as caught:
        dream.rollback()

    assert "no snapshot" in str(caught.value)


def test_an_unknown_snapshot_id_is_refused(workspace: Path) -> None:
    dream.create_snapshot(workspace)

    with pytest.raises(dream.DreamError) as caught:
        dream.rollback("19990101-000000")

    assert "unknown snapshot" in str(caught.value)


# --------------------------------------------------------------------------- #
# the CLI contract
# --------------------------------------------------------------------------- #
def test_dream_now_is_a_placeholder(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A pass that silently does nothing would be worse than one that is not there."""
    code = cli.main(["dream", "--now"])

    out = capsys.readouterr().out
    assert code == 0
    assert "planned in P4.2" in out
    assert " --snapshot" in out, "the message says what does exist"
    assert dream.list_snapshots() == [], "--now must not run a pass"
    assert dream.DreamJournal().read() == [], "--now must not write to the journal"


def test_dream_snapshot_from_the_cli(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["dream", "--snapshot"])

    out = capsys.readouterr().out
    assert code == 0
    assert "snapshot:" in out
    assert len(dream.list_snapshots()) == 1


def test_dream_rollback_refuses_without_a_terminal(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dream.create_snapshot(workspace)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)

    code = cli.main(["dream", "--rollback"])

    assert code != 0
    assert "without a terminal" in capsys.readouterr().err


def test_dream_rollback_with_yes_goes_through(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dream.create_snapshot(workspace)
    (workspace / "docs" / "guide.md").write_text("REECRIT", encoding="utf-8")

    code = cli.main(["dream", "--rollback", "--yes"])

    assert code == 0
    assert "restored 3 file(s)" in capsys.readouterr().out
    assert (workspace / "docs" / "guide.md").read_text(encoding="utf-8").startswith("# guide")


def test_dream_journal_from_the_cli(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    journal = dream.DreamJournal()
    journal.append({"type": "write", "path": "docs/guide.md", "reason": "compiled"})

    code = cli.main(["dream", "--journal"])

    out = capsys.readouterr().out
    assert code == 0
    assert "docs/guide.md" in out
    assert "compiled" in out
