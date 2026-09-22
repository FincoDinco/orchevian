"""Rendered images share their original's lifecycle; per-chat visual mode is explicit."""
from datetime import datetime

VERSION = 5


def apply(conn):
    if conn.execute('SELECT 1 FROM schema_migrations WHERE version = ?', (VERSION,)).fetchone():
        return
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS document_visuals (
            document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
            project_document_id TEXT REFERENCES project_documents(id) ON DELETE CASCADE,
            image_index INTEGER NOT NULL,
            location TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            data BLOB NOT NULL,
            CHECK ((document_id IS NULL) != (project_document_id IS NULL))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_visual
            ON document_visuals(document_id, image_index);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_project_visual
            ON document_visuals(project_document_id, image_index);
        CREATE TABLE IF NOT EXISTS visual_preferences (
            conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            use_images INTEGER NOT NULL DEFAULT 1
        );
    ''')
    conn.execute('INSERT INTO schema_migrations VALUES (?, ?)',
                 (VERSION, datetime.now().isoformat()))
