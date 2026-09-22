from datetime import datetime

VERSION = 3


def apply(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,)).fetchone():
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            message_id INTEGER REFERENCES messages(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            data BLOB NOT NULL,
            segments TEXT NOT NULL,
            warning TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_documents_conversation ON documents(conversation_id);
        CREATE TABLE IF NOT EXISTS document_context (
            message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sources TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO schema_migrations VALUES (?, ?)", (VERSION, datetime.now().isoformat())
    )
