"""SQLite store with a full-text index (Jalon 3).

One schema version (1), applied in a transaction, idempotent: reopening an
existing database re-runs the same ``IF NOT EXISTS`` statements and adds
nothing. A database whose ``schema_version`` is newer than this build is
refused with :class:`StoreError` - never downgraded silently.

Layout: ``$UH_HOME/uh.db`` (``UH_HOME`` defaults to ``~/.uh``), WAL mode,
foreign keys ON so deleting a session cascades to its messages. The FTS index
is an *external content* table over ``messages.content``, kept in sync by the
three triggers below; that is why deleting a message must use the
``'delete'`` command form, not a plain row delete.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1

# Exact DDL, in order. Each entry is one statement.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
)""",
    """CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}'
)""",
    """CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    tool_name   TEXT,
    tool_calls  TEXT,
    created_at  TEXT NOT NULL
)""",
    """CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='messages', content_rowid='id'
)""",
    """CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END""",
    """CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
END""",
    """CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END""",
)


class StoreError(RuntimeError):
    """Opening, migrating or querying the store failed. Never silently degraded."""


def uh_home() -> Path:
    raw = os.environ.get("UH_HOME")
    return (Path(raw).expanduser() if raw else Path.home() / ".uh").resolve()


def default_store_path() -> Path:
    return uh_home() / "uh.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    """Short slug ``YYYYMMDD-HHMM-xxxx`` (UTC); uniqueness is checked in the store."""
    return f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}-{secrets.token_hex(2)}"


def short_id(session_id: str, length: int = 8) -> str:
    """Raw prefix of the id (kept for callers that want it)."""
    return (session_id or "")[:length]


def display_id(session_id: str) -> str:
    """Short form shown in lists: time + random suffix, i.e. ``1827-abcd``.

    The id starts with ``YYYYMMDD``, so the first 8 characters are only the
    date: two sessions created the same day would look identical. The time and
    the random suffix are what actually distinguish a session.
    """
    return session_id[9:] if len(session_id) > 9 else (session_id or "")


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: quoted tokens, implicit AND."""
    tokens = re.findall(r"\w+", text or "")
    return " ".join(f'"{token}"' for token in tokens)


class Store:
    """Thin, explicit SQLite wrapper. Nothing here swallows an error."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path).expanduser().resolve() if path else default_store_path()
        if self.path.exists() and self.path.is_dir():
            raise StoreError(f"store path is a directory: {self.path}")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StoreError(f"cannot create store directory {self.path.parent}: {exc}") from exc

        try:
            self._conn: Optional[sqlite3.Connection] = sqlite3.connect(str(self.path))
        except sqlite3.Error as exc:
            raise StoreError(f"cannot open store {self.path}: {exc}") from exc

        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()
        except sqlite3.Error as exc:
            self._conn.close()
            self._conn = None
            raise StoreError(f"cannot initialise store {self.path}: {exc}") from exc

    # -- lifecycle --------------------------------------------------------- #
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("store is closed")
        return self._conn

    def migrate(self) -> int:
        """Apply the schema (idempotent). Returns the resulting schema version."""
        self._migrate()
        return self.schema_version

    def _migrate(self) -> None:
        try:
            with self.conn:
                for statement in SCHEMA_STATEMENTS:
                    self.conn.execute(statement)
                row = self.conn.execute("SELECT version FROM schema_version").fetchone()
                if row is None:
                    self.conn.execute(
                        "INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,)
                    )
                elif int(row["version"]) > SCHEMA_VERSION:
                    raise StoreError(
                        f"store schema v{row['version']} is newer than this build "
                        f"(v{SCHEMA_VERSION}); upgrade uh-harness"
                    )
        except sqlite3.OperationalError as exc:
            if "fts5" in str(exc).lower():
                raise StoreError(
                    f"this Python/SQLite build has no FTS5 support ({exc}); "
                    "the store cannot be created"
                ) from exc
            raise

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"] or 0)

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.commit()
        finally:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- sessions ---------------------------------------------------------- #
    def create_session(
        self,
        title: str = "",
        model: str = "",
        provider: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        for _ in range(32):
            session_id = new_session_id()
            if self.get_session(session_id) is not None:
                continue
            now = _now()
            with self.conn:
                self.conn.execute(
                    "INSERT INTO sessions "
                    "(id, created_at, updated_at, title, model, provider, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        now,
                        now,
                        title or "",
                        model or "",
                        provider or "",
                        json.dumps(metadata or {}, ensure_ascii=False),
                    ),
                )
            return session_id
        raise StoreError("could not allocate a unique session id after 32 attempts")

    def get_session(self, session_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return self._session_dict(row) if row else None

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) "
            "AS message_count FROM sessions s ORDER BY s.updated_at DESC, s.id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [self._session_dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self.conn:
            cursor = self.conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cursor.rowcount > 0

    def rename_session(self, session_id: str, title: str) -> bool:
        """Set a session title. Same schema, no migration."""
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title or "", _now(), session_id),
            )
        return cursor.rowcount > 0

    def set_folder(self, session_id: str, folder: str) -> bool:
        """Put a session in a folder (``""`` removes it). No migration: metadata JSON.

        ``updated_at`` is deliberately untouched: filing a conversation is not
        talking in it, and re-ordering the sidebar by "last filed" would hide
        the sessions the user is actually working on.
        """
        session = self.get_session(session_id)
        if session is None:
            return False
        metadata = dict(session.get("metadata") or {})
        name = (folder or "").strip()[:60]
        if name:
            metadata["folder"] = name
        else:
            metadata.pop("folder", None)
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE sessions SET metadata = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), session_id),
            )
        return cursor.rowcount > 0

    def find_session_by_metadata(self, key: str, value: Any) -> Optional[dict]:
        """Linear scan over sessions' JSON metadata (no JSON1 dependency assumed)."""
        for session in self.list_sessions(limit=10_000):
            if session.get("metadata", {}).get(key) == value:
                return session
        return None

    def _session_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.get("metadata") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        return data

    # -- messages ---------------------------------------------------------- #
    def append_message(self, session_id: str, message: dict) -> None:
        if self.get_session(session_id) is None:
            raise StoreError(f"unknown session: {session_id}")
        role = str(message.get("role") or "")
        if not role:
            raise StoreError("message has no role")
        content = message.get("content")
        if content is None:
            content = ""
        tool_calls = message.get("tool_calls")
        payload = (
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None
        )
        now = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO messages "
                "(session_id, role, content, tool_name, tool_calls, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    role,
                    str(content),
                    message.get("name") if role == "tool" else None,
                    payload,
                    now,
                ),
            )
            self.conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
            )

    def append_messages(self, session_id: str, messages: Iterable[dict]) -> int:
        count = 0
        for message in messages:
            self.append_message(session_id, message)
            count += 1
        return count

    def get_messages(self, session_id: str, limit: Optional[int] = None) -> list[dict]:
        if limit is None:
            rows = self.conn.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (session_id, max(1, int(limit))),
            ).fetchall()
        return [self._message_dict(row) for row in rows]

    def _message_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        raw = data.pop("tool_calls", None)
        if raw:
            try:
                data["tool_calls"] = json.loads(raw)
            except json.JSONDecodeError:
                data["tool_calls"] = []
        if data.get("tool_name"):
            data["name"] = data["tool_name"]
        return data

    def message_count(self, session_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row["n"])

    # -- search ------------------------------------------------------------ #
    def search(self, query: str, limit: int = 20) -> list[dict]:
        match = _fts_query(query)
        if not match:
            return []
        rows = self.conn.execute(
            "SELECT m.session_id AS session_id, s.title AS title, s.updated_at AS updated_at, "
            "snippet(messages_fts, 0, '[', ']', '...', 12) AS snippet, "
            "bm25(messages_fts) AS rank "
            "FROM messages_fts "
            "JOIN messages m ON m.id = messages_fts.rowid "
            "JOIN sessions s ON s.id = m.session_id "
            "WHERE messages_fts MATCH ? "
            "ORDER BY rank ASC LIMIT ?",
            (match, max(1, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]
