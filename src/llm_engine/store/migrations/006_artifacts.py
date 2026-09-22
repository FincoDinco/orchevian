"""Generated versions and reusable specifications; existing conversations stay intact."""

from datetime import datetime

VERSION = 6


def apply(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,)).fetchone():
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id TEXT PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            batch_id TEXT NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            version INTEGER NOT NULL,
            spec TEXT NOT NULL,
            data BLOB NOT NULL,
            previews BLOB NOT NULL,
            content TEXT NOT NULL,
            warning TEXT NOT NULL,
            sources TEXT NOT NULL,
            UNIQUE(conversation_id, name, version)
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_chat ON artifacts(conversation_id);
        CREATE TABLE IF NOT EXISTS artifact_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            specs TEXT NOT NULL
        );
    """)
    conn.execute("INSERT INTO schema_migrations VALUES (?, ?)",
                 (VERSION, datetime.now().isoformat()))
