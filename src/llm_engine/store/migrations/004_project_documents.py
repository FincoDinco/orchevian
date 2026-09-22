"""Project-owned files; reply provenance remains in independent JSON snapshots."""

from datetime import datetime

VERSION = 4


def apply(conn):
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (VERSION,)).fetchone():
        return
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS project_documents (
            id TEXT PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            data BLOB NOT NULL,
            segments TEXT NOT NULL,
            warning TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
        );
        CREATE INDEX IF NOT EXISTS idx_project_documents_project
            ON project_documents(project_id);
        CREATE TABLE IF NOT EXISTS project_document_exclusions (
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            document_id TEXT NOT NULL REFERENCES project_documents(id) ON DELETE CASCADE,
            PRIMARY KEY (conversation_id, document_id)
        );
    """)
    conn.execute(
        "INSERT INTO schema_migrations VALUES (?, ?)", (VERSION, datetime.now().isoformat())
    )
