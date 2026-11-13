"""The system prompt forbids working around the sandbox.

The incident: write_file refused, the model reached for `terminal` and created
the directory anyway. The rule now lives where the model reads it, with the
sentence it must relay to the user.
"""

from __future__ import annotations

from pathlib import Path

from uh import console


def test_the_prompt_forbids_bypassing_a_refusal() -> None:
    content = console.system_message(Path("/tmp/projet"))["content"]

    assert "NEVER try to bypass it with `terminal`" in content


def test_the_prompt_carries_the_sentence_to_relay() -> None:
    content = console.system_message(Path("/tmp/projet"))["content"]

    assert "Le sandbox refuse" in content
    assert "`/workspace <path>`" in content
    assert "`~/Desktop`" in content and "`~/Documents`" in content


def test_the_prompt_still_names_the_workspace() -> None:
    workspace = Path("/tmp/projet")
    assert str(workspace) in console.system_message(workspace)["content"]


def test_a_workspace_change_refreshes_the_prompt(tmp_path: Path) -> None:
    from uh import config as uh_config

    first = tmp_path / "un"
    second = tmp_path / "deux"
    first.mkdir()
    second.mkdir()
    session = console.ChatSession(
        provider_factory=lambda kind, model: None,  # type: ignore[arg-type,return-value]
        provider_kind="stub",
        chain=[],
        model="stub:1b",
        toolsets={},
        workspace=first,
        cfg=uh_config.load(),
        stream=False,
        store=None,
        max_iterations=1,
    )

    session.set_workspace(str(second))

    content = session.history[0]["content"]
    assert str(second.resolve()) in content
    assert str(first.resolve()) not in content
    assert "NEVER try to bypass it" in content
