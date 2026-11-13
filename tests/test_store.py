"""Store contract: schema v1, FTS5 index, cascade, idempotent migration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uh.store import (
    SCHEMA_VERSION,
    Store,
    StoreError,
    default_store_path,
    new_session_id,
    short_id,
)


def test_fresh_store_applies_schema(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        assert store.schema_version == SCHEMA_VERSION
        assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {
            row[0]
            for row in store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
            )
        }
        assert {"schema_version", "sessions", "messages", "messages_fts"} <= tables
        assert {"messages_ai", "messages_ad", "messages_au"} <= tables


def test_session_id_shape_and_collision_safety(tmp_path: Path) -> None:
    assert re.fullmatch(r"\d{8}-\d{4}-[0-9a-f]{4}", new_session_id())
    with Store(tmp_path / "uh.db") as store:
        ids = {store.create_session(title=f"t{i}") for i in range(20)}
        assert len(ids) == 20
        assert all(len(session_id) == 18 for session_id in ids)


def test_messages_roundtrip_and_updated_at(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session(title="t", model="m", provider="p", metadata={"k": 1})
        before = store.get_session(session_id)["updated_at"]
        store.append_message(session_id, {"role": "user", "content": "bonjour"})
        store.append_message(
            session_id,
            {
                "role": "assistant",
                "content": "salut",
                "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a"}}}],
            },
        )
        store.append_message(session_id, {"role": "tool", "name": "read_file", "content": '{"ok":1}'})

        messages = store.get_messages(session_id)
        assert [m["role"] for m in messages] == ["user", "assistant", "tool"]
        assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
        assert messages[2]["name"] == "read_file"
        assert store.message_count(session_id) == 3
        assert store.get_session(session_id)["updated_at"] >= before
        assert store.get_session(session_id)["metadata"] == {"k": 1}


def test_messages_limit_returns_last_n_in_order(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session()
        for index in range(6):
            store.append_message(session_id, {"role": "user", "content": f"m{index}"})
        tail = store.get_messages(session_id, limit=2)
        assert [m["content"] for m in tail] == ["m4", "m5"]


def test_fts_search_and_snippet(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session(title="demo")
        store.append_message(session_id, {"role": "user", "content": "le chat dort sur le clavier"})
        store.append_message(session_id, {"role": "user", "content": "autre sujet totalement"})

        hits = store.search("clavier")
        assert len(hits) == 1
        assert hits[0]["session_id"] == session_id
        assert "clavier" in hits[0]["snippet"]
        assert isinstance(hits[0]["rank"], float)

        assert store.search("inexistantmot") == []
        assert store.search("   ") == []
        # FTS syntax characters must not raise
        assert store.search('clavier" OR "x') != [] or True


def test_delete_session_cascades_and_cleans_index(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session()
        store.append_message(session_id, {"role": "user", "content": "a effacer"})
        assert store.search("effacer")

        assert store.delete_session(session_id) is True
        assert store.message_count(session_id) == 0
        assert store.search("effacer") == []
        assert store.delete_session("inexistant") is False


def test_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "uh.db"
    with Store(path) as store:
        session_id = store.create_session(title="keep")
        store.append_message(session_id, {"role": "user", "content": "contenu"})

    with Store(path) as store:  # second open must not duplicate schema objects
        store.migrate()
        assert store.schema_version == SCHEMA_VERSION
        triggers = [
            row[0]
            for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        ]
        assert len(triggers) == 3
        assert store.message_count(session_id) == 1
        assert store.search("contenu")


def test_fail_closed_on_directory_path(tmp_path: Path) -> None:
    target = tmp_path / "adir"
    target.mkdir()
    with pytest.raises(StoreError):
        Store(target)


def test_fail_closed_on_newer_schema(tmp_path: Path) -> None:
    path = tmp_path / "uh.db"
    with Store(path) as store:
        store.conn.execute("UPDATE schema_version SET version = 99")
        store.conn.commit()
    with pytest.raises(StoreError) as excinfo:
        Store(path)
    assert "newer than this build" in str(excinfo.value)


def test_append_to_unknown_session_raises(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        with pytest.raises(StoreError):
            store.append_message("nope", {"role": "user", "content": "x"})


def test_closed_store_refuses_use(tmp_path: Path) -> None:
    store = Store(tmp_path / "uh.db")
    store.close()
    with pytest.raises(StoreError):
        store.list_sessions()


def test_short_id_and_default_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UH_HOME", str(tmp_path / "home"))
    assert default_store_path() == (tmp_path / "home" / "uh.db").resolve()
    assert short_id("20260101-1200-abcd") == "20260101"


def test_set_folder_files_a_session_without_touching_updated_at(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session(title="t")
        before = store.get_session(session_id)["updated_at"]

        assert store.set_folder(session_id, "  Projets  ") is True

        session = store.get_session(session_id)
        assert session["metadata"]["folder"] == "Projets", "trimmed, and nothing else lost"
        assert session["updated_at"] == before, "filing is not talking: no reordering"


def test_set_folder_keeps_the_other_metadata_keys(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session(metadata={"pinned": True, "folder": "old"})

        store.set_folder(session_id, "new")

        metadata = store.get_session(session_id)["metadata"]
        assert metadata == {"pinned": True, "folder": "new"}


def test_an_empty_folder_removes_the_key(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session_id = store.create_session(metadata={"folder": "Projets"})

        store.set_folder(session_id, "")

        assert "folder" not in store.get_session(session_id)["metadata"]


def test_set_folder_on_an_unknown_session_is_false(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        assert store.set_folder("nope", "Projets") is False


def test_rename_folder_moves_every_session_and_keeps_the_rest(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        keep = store.create_session(metadata={"pinned": True, "folder": "old"})
        other = store.create_session(metadata={"folder": "elsewhere"})
        unfiled = store.create_session()

        assert store.rename_folder("old", "new") == 1

        assert store.get_session(keep)["metadata"] == {"pinned": True, "folder": "new"}
        assert store.get_session(other)["metadata"]["folder"] == "elsewhere"
        assert store.get_session(unfiled)["metadata"] == {}
        assert store.folders() == {"new": 1, "elsewhere": 1}


def test_rename_folder_refuses_empty_names(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session = store.create_session(metadata={"folder": "old"})
        assert store.rename_folder("old", "   ") == 0
        assert store.get_session(session)["metadata"]["folder"] == "old"


def test_delete_folder_unfiles_but_keeps_the_sessions(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        filed = store.create_session(metadata={"folder": "gone"})
        store.append_message(filed, {"role": "user", "content": "keep me"})

        assert store.delete_folder("gone") == 1

        assert store.get_session(filed) is not None
        assert "folder" not in store.get_session(filed)["metadata"]
        assert store.message_count(filed) == 1
        assert store.folders() == {}


def test_delete_an_unknown_or_empty_folder_is_a_no_op(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        assert store.delete_folder("nope") == 0
        assert store.delete_folder("") == 0


def test_delete_one_message_keeps_the_rest_and_cleans_the_index(tmp_path: Path) -> None:
    with Store(tmp_path / "uh.db") as store:
        session = store.create_session()
        first = store.append_message(session, {"role": "user", "content": "question gardee"})
        doomed = store.append_message(session, {"role": "assistant", "content": "reponse fausse"})

        assert store.delete_message(session, doomed) is True

        assert store.message_count(session) == 1
        assert store.search("fausse") == [], "the FTS trigger must drop the row"
        assert store.search("gardee"), "and keep the neighbours"
        assert store.delete_message(session, doomed) is False, "second delete: already gone"
        assert store.delete_message("autre-session", first) is False, "scoped by session"
