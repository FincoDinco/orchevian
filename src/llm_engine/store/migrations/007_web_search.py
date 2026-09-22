"""Retain web evidence per user turn; regeneration replaces that turn's evidence."""

from datetime import datetime

VERSION = 7


def apply(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,)).fetchone():
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS web_searches (
            message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            report TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_web_chat ON web_searches(conversation_id)")
    conn.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                 (VERSION, datetime.now().isoformat()))
