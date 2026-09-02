from __future__ import annotations

import sqlite3
from datetime import datetime

VERSION = 2

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_id "
    "ON messages (conversation_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_updated_at "
    "ON conversations (updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_project_id_updated_at "
    "ON conversations (project_id, updated_at DESC)",
)


def apply(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = ?",
        (VERSION,),
    ).fetchone()
    if row is not None:
        return
    for sql in _INDEXES:
        conn.execute(sql)
    conn.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
        (VERSION, datetime.now().isoformat()),
    )
