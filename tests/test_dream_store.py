"""The three buttons behind a dream: Effacer, Stocker, Exaucer.

Every action writes inside UH_HOME. A stamp from a URL is checked against a
strict pattern before it becomes a path, so nothing can walk out of the folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import dream_store

REPORT = """# Dream pass - 2026-11-13T03:00:00+00:00

- status: **ok**
- wall time: 163.5s
- tokens: 2063 (in 800 / out 1263)

## Ideas
1. **Evolver framework impact** - persistent agent experience
2. **LiteLLM MCP toolsets** - breaking changes in MLOps
"""


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UH_HOME", str(tmp_path))
    folder = tmp_path / "dreams" / "passes" / "20261113-030000"
    folder.mkdir(parents=True)
    (folder / "report.md").write_text(REPORT, encoding="utf-8")
    return tmp_path


def test_the_list_puts_the_newest_first(_home: Path) -> None:
    older = _home / "dreams" / "passes" / "20261112-030000"
    older.mkdir()
    (older / "report.md").write_text("- status: ok\n", encoding="utf-8")

    dreams = dream_store.list_dreams()

    assert [entry["stamp"] for entry in dreams] == ["20261113-030000", "20261112-030000"]


def test_a_dream_reports_its_status_and_ideas() -> None:
    entry = dream_store.read_dream("20261113-030000")

    assert entry is not None
    assert entry["status"] == "ok"
    assert entry["tokens"] == 2063
    assert [idea["title"] for idea in entry["ideas"]] == [
        "Evolver framework impact",
        "LiteLLM MCP toolsets",
    ]


def test_a_stamp_that_is_not_one_is_refused() -> None:
    """The URL is not a path: ../../etc must never reach the filesystem."""
    assert dream_store.read_dream("../../etc") is None
    assert dream_store.delete("../../etc") is False
    assert dream_store.fulfill("../../etc") is None


def test_deleting_removes_the_folder() -> None:
    assert dream_store.delete("20261113-030000") is True
    assert not (_home_passes() / "20261113-030000").exists()


def _home_passes() -> Path:
    return dream_store.passes_dir()


def test_archiving_moves_instead_of_copying() -> None:
    path = dream_store.archive("20261113-030000")

    assert path is not None and "archived" in path
    assert not (_home_passes() / "20261113-030000").exists(), "a move, not a copy"
    assert (dream_store.archived_dir() / "20261113-030000" / "report.md").is_file()


def test_fulfilling_writes_a_plan_and_changes_nothing_else() -> None:
    path = dream_store.fulfill("20261113-030000")

    assert path is not None
    plan = Path(path).read_text(encoding="utf-8")
    assert "Nothing in this file has been applied" in plan
    assert "[ ] **Evolver framework impact**" in plan
    assert (_home_passes() / "20261113-030000" / "report.md").is_file(), "the dream stays"


def test_a_dream_without_ideas_says_so() -> None:
    folder = _home_passes() / "20261112-030000"
    folder.mkdir()
    (folder / "report.md").write_text("- status: ok\n", encoding="utf-8")

    plan = Path(dream_store.fulfill("20261112-030000") or "").read_text(encoding="utf-8")

    assert "no idea to act on" in plan


def test_an_unknown_dream_is_a_404_not_a_crash() -> None:
    assert dream_store.read_dream("20990101-000000") is None
    assert dream_store.archive("20990101-000000") is None
