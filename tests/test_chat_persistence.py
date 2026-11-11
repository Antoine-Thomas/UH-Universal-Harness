"""uh chat persistence: sessions created, appended, or skipped (--no-save)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from uh import cli
from uh import config as uh_config
from uh.store import Store, display_id


class StubProvider:
    model = "stub:1b"

    def __init__(self, answer: str = "bonjour a toi") -> None:
        self.answer = answer

    def chat(self, messages, tools, *, on_token=None):
        if on_token is not None:
            on_token(self.answer)
        return {"role": "assistant", "content": self.answer, "tool_calls": []}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("UH_HOME", str(home))
    for name in uh_config.ENV_KEYS.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_make_provider", lambda kind, model: StubProvider())
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    return home


def test_once_with_prompt_creates_a_session(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "chat",
            "dis bonjour",
            "--once",
            "--model",
            "stub:1b",
            "--workspace",
            str(tmp_path),
            "--no-stream",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "bonjour a toi" in captured.out
    assert "session" in captured.err

    with Store(home / "uh.db") as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        session = sessions[0]
        assert session["title"] == "dis bonjour"
        assert session["model"] == "stub:1b"
        messages = store.get_messages(session["id"])
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "dis bonjour"
    assert messages[1]["content"] == "bonjour a toi"


def test_no_save_writes_nothing(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [
            "chat",
            "discret",
            "--once",
            "--no-save",
            "--model",
            "stub:1b",
            "--workspace",
            str(tmp_path),
            "--no-stream",
        ]
    )

    assert code == 0
    assert "bonjour a toi" in capsys.readouterr().out
    assert not (home / "uh.db").exists()


def test_session_flag_appends_to_existing_session(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with Store(home / "uh.db") as store:
        session_id = store.create_session(title="deja", model="stub:1b", provider="ollama")
        store.append_message(session_id, {"role": "user", "content": "je m'appelle Camille"})
        store.append_message(session_id, {"role": "assistant", "content": "enchanté"})

    code = cli.main(
        [
            "chat",
            "--session",
            display_id(session_id),
            "quel est mon prenom ?",
            "--once",
            "--model",
            "stub:1b",
            "--workspace",
            str(tmp_path),
            "--no-stream",
        ]
    )

    assert code == 0
    with Store(home / "uh.db") as store:
        assert store.get_session(session_id) is not None
        assert store.message_count(session_id) == 4
        sessions = store.list_sessions()
    assert len(sessions) == 1


def test_unknown_session_exits_one(home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        ["chat", "--session", "nope1234", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "no session matching" in captured.err


def test_session_plus_no_save_is_refused(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        ["chat", "--session", "abc", "--no-save", "--once", "--model", "stub:1b", "--workspace", str(tmp_path)]
    )
    assert code == 1
    assert "--session needs persistence" in capsys.readouterr().err
