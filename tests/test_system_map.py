"""The map the agent is given: the paths must come from the code, not a memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import config as uh_config
from uh import dream_store, skills, system_map
from uh.store import default_store_path


def test_the_map_names_every_place_that_matters() -> None:
    text = system_map.render()

    for path in (
        str(default_store_path()),
        str(dream_store.passes_dir()),
        str(dream_store.archived_dir()),
        str(dream_store.actions_dir()),
        str(skills.user_dir()),
        str(system_map.outputs_dir()),
    ):
        assert path in text, f"the map must name {path}"


def test_the_brief_is_short_enough_to_ride_on_every_turn() -> None:
    block = system_map.brief()

    assert block.count("\n") <= 15, "a map that costs a page per turn is a wall"
    assert "outputs" in block and "dreams (passes)" in block


def test_the_workspace_appears_only_when_there_is_one(tmp_path: Path) -> None:
    workspace = tmp_path / "some-workspace"
    workspace.mkdir()

    assert str(workspace) not in system_map.brief()

    assert str(workspace) in system_map.brief(workspace=workspace)


def test_outputs_can_be_moved_without_touching_the_code(tmp_path: Path) -> None:
    """The Desktop is a Windows habit, not a rule: the path is a setting."""

    class Settings:
        outputs_dir = str(tmp_path / "deliveries")

    assert system_map.outputs_dir(Settings()) == tmp_path / "deliveries"
    assert system_map.outputs_dir(None).name == "outputs"


def test_the_system_prompt_carries_the_map(tmp_path: Path) -> None:
    """The thing that answers "where are my dreams" must have been told."""
    from uh.console import system_message

    content = system_message(tmp_path)["content"]

    assert str(default_store_path()) in content
    assert str(dream_store.passes_dir()) in content
    assert "never guess" in content.lower() or "authoritative" in content.lower()


def test_writing_the_map_leaves_a_file_next_to_the_config(tmp_path: Path) -> None:
    written = system_map.write()

    assert written.is_file()
    assert "# UH system map" in written.read_text(encoding="utf-8")
    assert written.parent == uh_config.uh_home()


@pytest.mark.parametrize("entry_label", ["chats", "dreams (actions)", "outputs"])
def test_each_entry_says_what_it_holds(entry_label: str) -> None:
    rows = {row["label"]: row for row in system_map.entries()}

    assert rows[entry_label]["note"], "a path without a purpose is a riddle"
