from __future__ import annotations

from datetime import datetime
from types import EllipsisType
from typing import Any

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    Conversation,
    ConversationSummary,
    ModelRef,
    Project,
)
from llm_engine.store.sqlite import SqliteStore

_UNFILTERED: EllipsisType = ...


def _now() -> str:
    return datetime.now().isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _require_dt(value: str | None) -> datetime:
    parsed = _parse_dt(value)
    if parsed is None:
        raise EngineError("not_found", "missing timestamp")
    return parsed


def _as_model(backend: str | None, name: str | None) -> ModelRef | None:
    if not backend or not name or not backend.strip() or not name.strip():
        return None
    try:
        return ModelRef(BackendName(backend), name)
    except ValueError:
        return None


def _model_columns(model: ModelRef | None) -> tuple[str, str]:
    if model is None:
        return "", ""
    return model.name, str(model.backend)


class LibraryService:
    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def list_conversations(
        self,
        project_id: int | None | EllipsisType = _UNFILTERED,
        query: str | None = None,
    ) -> list[ConversationSummary]:
        sql = (
            "SELECT c.id, c.title, c.model_name, c.backend, c.project_id, "
            "c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) "
            "AS message_count "
            "FROM conversations c WHERE 1 = 1"
        )
        params: list[object] = []
        if project_id is not _UNFILTERED:
            if project_id is None:
                sql += " AND c.project_id IS NULL"
            else:
                sql += " AND c.project_id = ?"
                params.append(project_id)
        if query is not None and query.strip():
            sql += " AND c.title LIKE ? COLLATE NOCASE"
            params.append(f"%{query.strip()}%")
        sql += " ORDER BY c.updated_at DESC"
        with self._store.locked() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def get_conversation(self, id: int) -> Conversation:
        with self._store.locked() as conn:
            row = conn.execute(
                "SELECT id, title, model_name, backend, system_prompt, project_id, "
                "created_at, updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id = conversations.id) "
                "AS message_count "
                "FROM conversations WHERE id = ?",
                (id,),
            ).fetchone()
            if row is None:
                raise EngineError("not_found", f"conversation {id} not found")
            messages = conn.execute(
                "SELECT role, content, tokens_per_sec, elapsed, created_at "
                "FROM messages WHERE conversation_id = ? ORDER BY id",
                (id,),
            ).fetchall()
        return Conversation(
            summary=self._summary_from_row(row),
            system_prompt=row["system_prompt"] or "",
            messages=tuple(self._turn_from_row(m) for m in messages),
        )

    def create_conversation(
        self,
        project_id: int | None = None,
        model: ModelRef | None = None,
    ) -> Conversation:
        now = _now()
        system_prompt = ""
        resolved_project: int | None = project_id
        if project_id is not None:
            project = self._get_project_row(project_id)
            system_prompt = project.instructions
            if model is None:
                model = project.default_model
        model_name, backend = _model_columns(model)
        with self._store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO conversations (title, model_name, backend, system_prompt, "
                "project_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("New Chat", model_name, backend, system_prompt, resolved_project, now, now),
            )
            row_id = cur.lastrowid
        if row_id is None:
            raise EngineError("not_found", "failed to create conversation")
        return self.get_conversation(int(row_id))

    def rename(self, id: int, title: str) -> None:
        self._update_conversation(id, title=title)

    def delete_conversation(self, id: int) -> None:
        with self._store.transaction() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (id,))
            if cur.rowcount == 0:
                raise EngineError("not_found", f"conversation {id} not found")

    def move(self, id: int, project_id: int | None) -> None:
        if project_id is not None:
            self._get_project_row(project_id)
        self._update_conversation(id, project_id=project_id)

    def list_projects(self) -> list[Project]:
        with self._store.locked() as conn:
            rows = conn.execute(
                "SELECT id, name, instructions, model_name, backend, created_at "
                "FROM projects ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [self._project_from_row(row) for row in rows]

    def create_project(
        self,
        name: str,
        instructions: str = "",
        model: ModelRef | None = None,
    ) -> Project:
        now = _now()
        model_name, backend = _model_columns(model)
        with self._store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO projects (name, instructions, model_name, backend, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, instructions, model_name, backend, now),
            )
            row_id = cur.lastrowid
        if row_id is None:
            raise EngineError("not_found", "failed to create project")
        return self._get_project_row(int(row_id))

    def update_project(self, id: int, **fields: Any) -> Project:
        allowed = {"name", "instructions", "model"}
        unknown = frozenset(fields) - allowed
        if unknown:
            raise EngineError("config_invalid", f"unknown fields: {sorted(unknown)}")
        assignments: dict[str, object] = {}
        if "name" in fields:
            assignments["name"] = fields["name"]
        if "instructions" in fields:
            assignments["instructions"] = fields["instructions"]
        if "model" in fields:
            model_name, backend = _model_columns(fields["model"])
            assignments["model_name"] = model_name
            assignments["backend"] = backend
        if assignments:
            sets = ", ".join(f"{column} = ?" for column in assignments)
            with self._store.transaction() as conn:
                cur = conn.execute(
                    f"UPDATE projects SET {sets} WHERE id = ?",
                    (*assignments.values(), id),
                )
                if cur.rowcount == 0:
                    raise EngineError("not_found", f"project {id} not found")
        return self._get_project_row(id)

    def delete_project(self, id: int) -> None:
        with self._store.transaction() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (id,))
            if cur.rowcount == 0:
                raise EngineError("not_found", f"project {id} not found")

    def _update_conversation(self, id: int, **fields: Any) -> None:
        assignments: dict[str, object] = dict(fields)
        assignments["updated_at"] = _now()
        sets = ", ".join(f"{column} = ?" for column in assignments)
        with self._store.transaction() as conn:
            cur = conn.execute(
                f"UPDATE conversations SET {sets} WHERE id = ?",
                (*assignments.values(), id),
            )
            if cur.rowcount == 0:
                raise EngineError("not_found", f"conversation {id} not found")

    def _get_project_row(self, id: int) -> Project:
        with self._store.locked() as conn:
            row = conn.execute(
                "SELECT id, name, instructions, model_name, backend, created_at "
                "FROM projects WHERE id = ?",
                (id,),
            ).fetchone()
        if row is None:
            raise EngineError("not_found", f"project {id} not found")
        return self._project_from_row(row)

    def _summary_from_row(self, row: Any) -> ConversationSummary:
        return ConversationSummary(
            id=int(row["id"]),
            title=row["title"],
            model=_as_model(row["backend"], row["model_name"]),
            project_id=row["project_id"],
            message_count=int(row["message_count"]),
            updated_at=_require_dt(row["updated_at"]),
            created_at=_require_dt(row["created_at"]),
        )

    def _project_from_row(self, row: Any) -> Project:
        return Project(
            id=int(row["id"]),
            name=row["name"],
            instructions=row["instructions"] or "",
            default_model=_as_model(row["backend"], row["model_name"]),
            created_at=_require_dt(row["created_at"]),
        )

    def _turn_from_row(self, row: Any) -> ChatTurn:
        return ChatTurn(
            role=row["role"],
            content=row["content"],
            tokens_per_sec=row["tokens_per_sec"],
            elapsed_s=row["elapsed"],
            created_at=_parse_dt(row["created_at"]),
        )
