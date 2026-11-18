"""P1-11: keep, trash and rate one generated image — without becoming a delete API.

The transcript draws a figure from its data URL. The bytes on disk are the tool's
real output, so "Poubelle" has to touch something real. Every action therefore
resolves the file from the artifact's provenance and refuses anything that does
not live inside UHB's runs root: a tool message is data the model wrote, not a
capability to delete an operator's file.

Measured before this lot: the artifact payload carried mime/bytes/data_url only,
so a delete button had nothing to aim at, and a vote had no journal at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uh import artifacts as uh_artifacts


@pytest.fixture()
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runs"
    run = root / "run-20260101-120000-abcdef"
    run.mkdir(parents=True)
    monkeypatch.setattr(uh_artifacts, "RUNS_ROOT", root)
    monkeypatch.setattr(uh_artifacts, "RATINGS_FILE", tmp_path / "ratings.jsonl")
    return root


def make_image(root: Path, name: str = "chart.png") -> Path:
    folder = root / "run-20260101-120000-abcdef"
    path = folder / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    return path


def provenance(path: Path) -> dict:
    return {"path": str(path), "run_dir": str(path.parent), "relative": path.name}


# --------------------------------------------------------------------------- #
# keep
# --------------------------------------------------------------------------- #
def test_keep_marks_the_file_without_moving_it(runs: Path) -> None:
    path = make_image(runs)

    result = uh_artifacts.keep(provenance(path))

    assert result["kept"] is True
    assert path.exists(), "keeping an image never removes it from the transcript"
    marker = path.with_name(path.name + ".keep")
    assert marker.exists(), "the retention policy (P1-12) reads this marker"
    assert uh_artifacts.is_kept(provenance(path)) is True


# --------------------------------------------------------------------------- #
# trash
# --------------------------------------------------------------------------- #
def test_trash_moves_the_file_and_records_where_it_came_from(runs: Path) -> None:
    path = make_image(runs)
    origin = str(path)

    result = uh_artifacts.trash(provenance(path))

    assert result["trashed"] is True
    assert not path.exists(), "the run directory no longer holds it"
    moved = Path(result["moved_to"])
    assert moved.exists() and moved.parent.name == "_trash" or moved.parent.parent.name == "_trash"
    sidecar = moved.with_name(moved.name + ".json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["from"] == origin
    assert uh_artifacts.trash_count() >= 1


def test_a_second_trash_never_overwrites_the_first(runs: Path) -> None:
    first = make_image(runs, "chart.png")
    uh_artifacts.trash(provenance(first))
    make_image(runs, "chart.png")

    second = uh_artifacts.trash(provenance(make_image(runs, "chart.png")))

    assert "chart-1.png" in second["moved_to"]


def test_empty_trash_removes_only_the_trash(runs: Path) -> None:
    path = make_image(runs)
    sibling = make_image(runs, "other.png")
    uh_artifacts.trash(provenance(path))

    result = uh_artifacts.empty_trash()

    assert result["removed"] >= 1
    assert sibling.exists(), "a run directory is never swept by emptying the trash"


# --------------------------------------------------------------------------- #
# the guard: nothing outside the runs root
# --------------------------------------------------------------------------- #
def test_a_path_outside_the_runs_root_is_refused(runs: Path, tmp_path: Path) -> None:
    outsider = tmp_path / "Documents" / "important.png"
    outsider.parent.mkdir()
    outsider.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(uh_artifacts.ArtifactActionError):
        uh_artifacts.trash({"path": str(outsider)})

    assert outsider.exists(), "a refused action touches nothing"


def test_a_traversal_through_the_provenance_is_refused(runs: Path, tmp_path: Path) -> None:
    outsider = tmp_path / "secret.png"
    outsider.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(uh_artifacts.ArtifactActionError):
        uh_artifacts.keep({"run_dir": str(runs), "relative": "../../secret.png"})


def test_a_missing_file_is_a_refusal_not_a_500(runs: Path) -> None:
    gone = runs / "run-20260101-120000-abcdef" / "gone.png"

    with pytest.raises(uh_artifacts.ArtifactActionError):
        uh_artifacts.keep({"path": str(gone)})


def test_an_empty_provenance_is_refused(runs: Path) -> None:
    with pytest.raises(uh_artifacts.ArtifactActionError):
        uh_artifacts.keep({})


# --------------------------------------------------------------------------- #
# rate
# --------------------------------------------------------------------------- #
def test_a_vote_appends_one_line_to_the_local_journal(runs: Path, tmp_path: Path) -> None:
    path = make_image(runs)

    uh_artifacts.rate(provenance(path), "up", session_id="s1", message_id=7, index=0)

    lines = (tmp_path / "ratings.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert line["vote"] == "up" and line["file"] == str(path)
    assert line["session"] == "s1" and line["message"] == 7


def test_a_vote_on_a_gone_file_is_still_recorded(runs: Path, tmp_path: Path) -> None:
    """The opinion is worth keeping even when the image was already trashed."""
    gone = runs / "run-20260101-120000-abcdef" / "gone.png"

    result = uh_artifacts.rate({"path": str(gone)}, "down")

    assert result["rated"] == "down"
    assert (tmp_path / "ratings.jsonl").exists()


def test_a_vote_must_be_up_or_down(runs: Path) -> None:
    path = make_image(runs)

    with pytest.raises(uh_artifacts.ArtifactActionError):
        uh_artifacts.rate(provenance(path), "maybe")
