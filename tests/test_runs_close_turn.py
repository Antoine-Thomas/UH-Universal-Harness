"""P3-16b, part 3: the runs of a finished turn are closed, and never at the cost of it.

The order is the point: a turn answers, the interface reads the images, *then* the runs
are closed. Closing earlier would delete the files the bubble is still drawing, which is
exactly the mistake Gate B punished. And a closure that fails is bookkeeping: it is
logged, never raised into the answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uh import uhb_runs

APP = Path(__file__).resolve().parents[1] / "uh" / "web" / "app.py"


def test_close_many_closes_every_run_with_its_own_keep_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        uhb_runs, "close", lambda run, keep, summary="": seen.append((run, keep)) or {"ok": True}
    )

    results = uhb_runs.close_many(
        [{"run": "run-a", "keep": True}, {"run": "run-b", "keep": False}, {"run": ""}]
    )

    assert seen == [("run-a", True), ("run-b", False)], "un run vide n'est pas un run"
    assert len(results) == 2 and all(item["ok"] for item in results)


def test_a_closing_that_fails_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(run: str, keep: bool, summary: str = "") -> dict:
        raise RuntimeError("UHB a disparu")

    monkeypatch.setattr(uhb_runs, "close", boom)

    results = uhb_runs.close_many([{"run": "run-a", "keep": False}])

    assert results[0]["ok"] is False
    assert "UHB a disparu" in results[0]["reason"]
    assert results[0]["run"] == "run-a"


def test_uhb_being_down_is_a_reason_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_UHB_URL", "http://127.0.0.1:9")  # nothing listens there
    data = uhb_runs.status()
    assert data["ok"] is False and "injoignable" in data["reason"]
    assert data["runs"] == []


def test_the_turn_closes_its_runs_after_the_media_purge() -> None:
    """The order written in the code, checked as text because it is a sequence, not a value."""
    source = APP.read_text(encoding="utf-8")
    purge = source.index("uh_media.purge(")
    close = source.index("uhb_runs.close_many(")
    end = source.index('"__end__"')
    assert end < purge < close, "clore les runs avant la fin du tour tuerait les images lues"

    block = source[close - 400 : close + 400]
    assert "try:" in block and "except Exception" in block, (
        "la cloture est de la comptabilite : elle ne doit jamais faire echouer un tour"
    )


def test_the_run_id_is_collected_from_the_tool_result_payload() -> None:
    source = APP.read_text(encoding="utf-8")
    assert 'if name == "execute_code"' in source
    assert '"run"' in source and 'payload.get("keep")' in source
    # Le nom seul, jamais un chemin complet : UHB resout dans sa racine.
    assert 'payload.get("run_dir")' in source


def test_the_workspace_of_a_turn_is_the_session_one() -> None:
    source = APP.read_text(encoding="utf-8")
    assert '(session.get("metadata") or {}).get("workspace")' in source
    # Le workspace traverse `app.state.turn_workspace` : un nouveau parametre de
    # `_toolsets` a casse un stub de test et tue le fil du tour (604 s sans reponse).
    assert "app.state.turn_workspace = _workspace" in source
    assert 'getattr(app.state, "turn_workspace", None)' in source
