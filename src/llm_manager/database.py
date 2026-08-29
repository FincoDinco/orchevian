import sqlite3
from datetime import datetime

from .config import get_config
from .models import ChatMessage, Conversation, Project, PromptTemplate

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    backend TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'New Chat',
    model_name TEXT NOT NULL DEFAULT '',
    backend TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tokens_per_sec REAL,
    elapsed REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS favorites (
    model_name TEXT NOT NULL,
    backend TEXT NOT NULL,
    pinned_at TEXT NOT NULL,
    PRIMARY KEY (model_name, backend)
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    user_prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(get_config().db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)")}
    if "project_id" not in cols:
        conn.execute(
            "ALTER TABLE conversations ADD COLUMN project_id INTEGER "
            "REFERENCES projects(id) ON DELETE SET NULL"
        )


def _now() -> str:
    return datetime.now().isoformat()


# --- Conversations ---

def list_conversations() -> list[Conversation]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        msgs = _load_messages(r["id"])
        result.append(Conversation(
            id=r["id"], title=r["title"], model_name=r["model_name"],
            backend=r["backend"], system_prompt=r["system_prompt"],
            messages=msgs,
            project_id=r["project_id"],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        ))
    return result


def create_conversation(
    title: str = "New Chat",
    model_name: str = "",
    backend: str = "",
    system_prompt: str = "",
    project_id: int | None = None,
) -> Conversation:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, model_name, backend, system_prompt, project_id, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (title, model_name, backend, system_prompt, project_id, now, now),
        )
        conn.commit()
        row_id = cur.lastrowid
    return Conversation(
        id=row_id, title=title, model_name=model_name, backend=backend,
        system_prompt=system_prompt, project_id=project_id,
    )


def update_conversation(conv_id: int, **kwargs) -> None:
    allowed = {"title", "model_name", "backend", "system_prompt", "project_id"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE conversations SET {sets} WHERE id=?", (*fields.values(), conv_id))
        conn.commit()


def delete_conversation(conv_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        conn.commit()


def _load_messages(conv_id: int) -> list[ChatMessage]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY id",
            (conv_id,)
        ).fetchall()
    return [
        ChatMessage(role=r["role"], content=r["content"],
                    tokens_per_sec=r["tokens_per_sec"], elapsed=r["elapsed"])
        for r in rows
    ]


def add_message(conv_id: int, role: str, content: str, tokens_per_sec: float | None = None, elapsed: float | None = None) -> None:
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, tokens_per_sec, elapsed, created_at) VALUES (?,?,?,?,?,?)",
            (conv_id, role, content, tokens_per_sec, elapsed, now)
        )
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id))
        conn.commit()


def delete_last_messages(conv_id: int, n: int = 1) -> None:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
            (conv_id, n)
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.execute(f"DELETE FROM messages WHERE id IN ({','.join('?' * len(ids))})", ids)
            conn.commit()


# --- Favorites ---

def list_favorites() -> set[tuple[str, str]]:
    with _connect() as conn:
        rows = conn.execute("SELECT model_name, backend FROM favorites").fetchall()
    return {(r["model_name"], r["backend"]) for r in rows}


def toggle_favorite(model_name: str, backend: str) -> bool:
    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM favorites WHERE model_name=? AND backend=?", (model_name, backend)
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM favorites WHERE model_name=? AND backend=?", (model_name, backend))
            conn.commit()
            return False
        else:
            conn.execute(
                "INSERT INTO favorites (model_name, backend, pinned_at) VALUES (?,?,?)",
                (model_name, backend, _now())
            )
            conn.commit()
            return True


# --- Templates ---

def list_templates() -> list[PromptTemplate]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM templates ORDER BY name").fetchall()
    return [
        PromptTemplate(
            id=r["id"], name=r["name"], description=r["description"],
            system_prompt=r["system_prompt"], user_prompt=r["user_prompt"],
            created_at=datetime.fromisoformat(r["created_at"])
        )
        for r in rows
    ]


def save_template(name: str, description: str, system_prompt: str, user_prompt: str, template_id: int | None = None) -> int:
    now = _now()
    with _connect() as conn:
        if template_id is not None:
            conn.execute(
                "UPDATE templates SET name=?, description=?, system_prompt=?, user_prompt=? WHERE id=?",
                (name, description, system_prompt, user_prompt, template_id)
            )
            conn.commit()
            return template_id
        else:
            cur = conn.execute(
                "INSERT INTO templates (name, description, system_prompt, user_prompt, created_at) VALUES (?,?,?,?,?)",
                (name, description, system_prompt, user_prompt, now)
            )
            conn.commit()
            return cur.lastrowid


def delete_template(template_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM templates WHERE id=?", (template_id,))
        conn.commit()


# --- Projects ---

def list_projects() -> list[Project]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
    return [
        Project(
            id=r["id"], name=r["name"], instructions=r["instructions"],
            model_name=r["model_name"], backend=r["backend"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


def create_project(name: str, instructions: str = "", model_name: str = "", backend: str = "") -> Project:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, instructions, model_name, backend, created_at) "
            "VALUES (?,?,?,?,?)",
            (name, instructions, model_name, backend, now),
        )
        conn.commit()
        pid = cur.lastrowid
    return Project(id=pid, name=name, instructions=instructions, model_name=model_name, backend=backend)


def update_project(project_id: int, **kwargs) -> None:
    allowed = {"name", "instructions", "model_name", "backend"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with _connect() as conn:
        conn.execute(f"UPDATE projects SET {sets} WHERE id=?", (*fields.values(), project_id))
        conn.commit()


def delete_project(project_id: int) -> None:
    """Delete the project; its chats survive with project_id cleared (ON DELETE SET NULL)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
