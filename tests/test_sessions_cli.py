"""uh sessions / uh chat persistence: real store, stubbed provider."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import cli
from uh import config as uh_config
from uh.store import Store, display_id


class EchoProvider:
    """Answers with the size of the history it received, to prove it was reloaded."""

    model = "stub:1b"

    def __init__(self, answer: str = "ok") -> None:
        self.answer = answer
        self.seen: list[list[dict]] = []

    def chat(self, messages, tools, *, on_token=None):
        self.seen.append([dict(message) for message in messages])
        text = f"{self.answer} history={len(messages)}"
        if on_token is not None:
            on_token(text)
        return {"role": "assistant", "content": text, "tool_calls": []}


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    for name in uh_config.ENV_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return home / "uh.db"


def _seed(path: Path) -> str:
    with Store(path) as store:
        session_id = store.create_session(title="demo", model="stub:1b", provider="ollama")
        store.append_message(session_id, {"role": "user", "content": "bonjour le monde"})
        store.append_message(session_id, {"role": "assistant", "content": "salut"})
    return session_id


def test_sessions_list_empty(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["sessions", "list"])
    captured = capsys.readouterr()
    assert code == 0
    assert "no sessions" in captured.err
    assert captured.out == ""


def test_sessions_list_show_search(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session_id = _seed(db_path)

    assert cli.main(["sessions", "list"]) == 0
    listing = capsys.readouterr().out
    assert display_id(session_id) in listing
    assert "demo" in listing

    assert cli.main(["sessions", "show", session_id]) == 0
    shown = capsys.readouterr().out
    assert session_id in shown
    assert "bonjour le monde" in shown
    assert "salut" in shown

    assert cli.main(["sessions", "search", "bonjour"]) == 0
    hits = capsys.readouterr().out
    assert display_id(session_id) in hits
    assert "bonjour" in hits

    assert cli.main(["sessions", "search", "motabsent"]) == 0
    assert "no match" in capsys.readouterr().err


def test_sessions_short_id_prefix_resolves(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session_id = _seed(db_path)
    assert cli.main(["sessions", "show", display_id(session_id)]) == 0
    assert session_id in capsys.readouterr().out


def test_sessions_unknown_id_exits_one(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["sessions", "show", "deadbeef"])
    captured = capsys.readouterr()
    assert code == 1
    assert "no session matching" in captured.err
    assert "Traceback" not in captured.err


def test_sessions_delete_paths(db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session_id = _seed(db_path)

    assert cli.main(["sessions", "delete", session_id]) == 1  # no TTY, no --yes
    assert "needs a TTY" in capsys.readouterr().err
    assert "deleted" not in ""

    assert cli.main(["sessions", "delete", session_id, "--yes"]) == 0
    assert "deleted" in capsys.readouterr().err
    with Store(db_path) as store:
        assert store.get_session(session_id) is None

    assert cli.main(["sessions", "delete", session_id, "--yes"]) == 1


def test_sessions_resume_reloads_history(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    session_id = _seed(db_path)
    provider = EchoProvider()
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: provider)
    monkeypatch.setattr("sys.stdin", io.StringIO("quel est mon prenom ?\n"))

    code = cli.main(["sessions", "resume", session_id, "--no-stream"])

    captured = capsys.readouterr()
    assert code == 0
    assert "history=" in captured.out
    # system + (user, assistant) replayed + the new user message
    assert provider.seen[0][0]["role"] == "system"
    assert len(provider.seen[0]) == 4
    with Store(db_path) as store:
        messages = store.get_messages(session_id)
    assert len(messages) == 4
    assert messages[-2]["content"] == "quel est mon prenom ?"


def test_sessions_resume_fork_creates_a_new_session(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = _seed(db_path)
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: EchoProvider())
    monkeypatch.setattr("sys.stdin", io.StringIO("suite\n"))

    assert cli.main(["sessions", "resume", session_id, "--fork", "--no-stream"]) == 0

    with Store(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 2
        forked = [s for s in sessions if s["id"] != session_id][0]
        assert forked["metadata"].get("forked_from") == session_id
        assert len(store.get_messages(forked["id"])) == 4
