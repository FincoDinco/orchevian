from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore, backup_path_for, load_migrations

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "src" / "llm_engine" / "store" / "migrations"
SCHEMA = ROOT / "src" / "llm_engine" / "store" / "schema.sql"


def _copy_fixture(name: str, tmp_path: Path) -> Path:
    dest = tmp_path / "data.db"
    shutil.copy2(FIXTURES / name, dest)
    return dest


def _table_sql(path: Path, table: str) -> str:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row[0] if row else ""
    finally:
        conn.close()


def _columns(path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def _sequence(path: Path, table: str) -> int | None:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = ?",
            (table,),
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def test_001_source_never_drops() -> None:
    text = (MIGRATIONS / "001_baseline.py").read_text(encoding="utf-8")
    assert "DROP" not in text.upper()
    assert "CREATE TABLE IF NOT EXISTS schema_migrations" in text.replace("\n", " ")
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "DROP" not in schema.upper()
    assert "project_id" in schema


def test_fresh_db_applies_schema_sql(tmp_path: Path) -> None:
    db = tmp_path / "data.db"
    store = SqliteStore(db)
    version = store.schema_version()
    with store.locked() as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        conv_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()[0]
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
    store.close()
    assert version == 2
    assert "schema_migrations" in names
    assert "conversations" in names
    assert "project_id" in conv_sql
    reopened = SqliteStore(db)
    assert reopened.schema_version() == 2
    reopened.close()
    assert "idx_messages_conversation_id_id" in indexes
    assert "idx_conversations_updated_at" in indexes
    assert "idx_conversations_project_id_updated_at" in indexes
    assert not backup_path_for(db).exists()


def test_legacy_fixture_preserves_sqlite_sequence(tmp_path: Path) -> None:
    db = _copy_fixture("legacy_data.db", tmp_path)
    assert 4 in [
        row[0]
        for row in sqlite3.connect(db).execute("SELECT id FROM conversations")
    ]
    original_sql = _table_sql(db, "conversations")
    assert _sequence(db, "conversations") == 8
    assert _sequence(db, "messages") == 10
    assert _sequence(db, "projects") == 3

    store = SqliteStore(db)
    library = LibraryService(store)
    rows = library.list_conversations()
    assert len(rows) == 1
    assert rows[0].id == 4
    assert rows[0].title == "New Chat"
    assert rows[0].model is not None
    assert rows[0].model.name == "deepseek-coder-v2:16b"
    created = library.create_conversation()
    assert created.summary.id == 9
    store.close()

    assert _sequence(db, "conversations") == 9
    assert _table_sql(db, "conversations") == original_sql
    assert "CREATE TABLE conversations" in original_sql


def test_legacy_boot_writes_pre_engine_backup(tmp_path: Path) -> None:
    db = _copy_fixture("legacy_data.db", tmp_path)
    store = SqliteStore(db)
    store.close()
    backup = backup_path_for(db)
    assert backup.is_file()
    conn = sqlite3.connect(str(backup))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "schema_migrations" not in tables
        ids = [row[0] for row in conn.execute("SELECT id FROM conversations")]
        assert ids == [4]
        seq = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'conversations'"
        ).fetchone()[0]
        assert seq == 8
    finally:
        conn.close()

    # Second open must not replace the pre-engine backup with the migrated file.
    store = SqliteStore(db)
    store.close()
    conn = sqlite3.connect(str(backup))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "schema_migrations" not in tables
    finally:
        conn.close()


def test_pre_project_id_fixture_alters_without_drop(tmp_path: Path) -> None:
    db = _copy_fixture("pre_project_id.db", tmp_path)
    assert "project_id" not in _columns(db, "conversations")
    original_id_sql = _table_sql(db, "conversations")
    assert "project_id" not in original_id_sql

    store = SqliteStore(db)
    library = LibraryService(store)
    cols = _columns(db, "conversations")
    assert "project_id" in cols
    conv = library.get_conversation(1)
    assert conv.summary.title == "Legacy Chat"
    assert conv.summary.project_id is None
    assert conv.system_prompt == "be brief"
    assert [turn.content for turn in conv.messages] == ["hello from pre-project_id"]
    created = library.create_conversation()
    assert created.summary.id == 2
    store.close()
    migrated_sql = _table_sql(db, "conversations")
    assert "DROP" not in migrated_sql.upper()
    assert "project_id" in migrated_sql
    assert "title TEXT NOT NULL DEFAULT 'New Chat'" in migrated_sql
    assert _sequence(db, "conversations") == 2
    assert _sequence(db, "messages") == 1


def test_001_contains_1_is_noop(tmp_path: Path) -> None:
    db = _copy_fixture("legacy_data.db", tmp_path)
    store = SqliteStore(db)
    with store.locked() as conn:
        sql_before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()[0]
        versions_before = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations")
        }
    store.close()

    store = SqliteStore(db)
    with store.locked() as conn:
        sql_after = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()[0]
        versions_after = {
            row[0] for row in conn.execute("SELECT version FROM schema_migrations")
        }
        seq = conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'conversations'"
        ).fetchone()[0]
    store.close()
    assert sql_after == sql_before
    assert versions_before == versions_after == {1, 2}
    assert seq == 8


def test_001_else_noop_when_project_id_already_present(tmp_path: Path) -> None:
    db = tmp_path / "already.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            backend TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'New Chat',
            model_name TEXT NOT NULL DEFAULT '',
            backend TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            tokens_per_sec REAL,
            elapsed REAL,
            created_at TEXT NOT NULL
        );
        INSERT INTO conversations
            (id, title, model_name, backend, system_prompt, created_at, updated_at)
        VALUES (7, 'Keep Me', 'm', 'ollama', '', '2026-01-01T00:00:00', '2026-01-01T00:00:00');
        """
    )
    conn.execute("UPDATE sqlite_sequence SET seq = 7 WHERE name = 'conversations'")
    conn.commit()
    sql_before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
    ).fetchone()[0]
    conn.close()

    store = SqliteStore(db)
    library = LibraryService(store)
    created = library.create_conversation()
    assert created.summary.id == 8
    assert library.get_conversation(7).summary.title == "Keep Me"
    with store.locked() as opened:
        sql_after = opened.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'conversations'"
        ).fetchone()[0]
    store.close()
    assert sql_after == sql_before


def test_001_apply_returns_when_version_present(tmp_path: Path) -> None:
    db = tmp_path / "data.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (1, '2026-01-01T00:00:00')"
    )
    conn.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT 'New Chat',
            model_name TEXT NOT NULL DEFAULT '',
            backend TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    baseline = load_migrations()[0]
    baseline.apply(conn)
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
    cols = [row[1] for row in conn.execute("PRAGMA table_info(conversations)")]
    conn.close()
    assert versions == [1]
    assert "project_id" not in cols
