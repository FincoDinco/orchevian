from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

VERSION = 1

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (VERSION,),
    ).fetchone()
    if row is not None:
        return

    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "conversations" not in tables:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    else:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(conversations)")}
        if "project_id" not in cols:
            conn.execute(
                "ALTER TABLE conversations ADD COLUMN project_id INTEGER "
                "REFERENCES projects(id) ON DELETE SET NULL"
            )

    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (VERSION, datetime.now().isoformat()),
    )
