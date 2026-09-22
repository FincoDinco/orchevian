from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import threading
import types
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

MIGRATION_FILES = (
    "001_baseline.py", "002_indexes.py", "003_documents.py", "004_project_documents.py",
    "005_visual_documents.py", "006_artifacts.py", "007_web_search.py",
)
BACKUP_SUFFIX = ".bak-pre-engine"

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _load_migration(filename: str) -> types.ModuleType:
    path = _MIGRATIONS_DIR / filename
    # Filenames may start with digits; importlib needs a valid module name.
    spec = importlib.util.spec_from_file_location(
        f"llm_engine_store_migration_{path.stem}",
        path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load migration {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_migrations() -> tuple[types.ModuleType, ...]:
    return tuple(_load_migration(name) for name in MIGRATION_FILES)


def _schema_migrations_exists(path: Path) -> bool:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _backup_pre_engine(path: Path) -> Path:
    backup = path.parent / f"{path.name}{BACKUP_SUFFIX}"
    shutil.copy2(path, backup)
    return backup


class SqliteStore:
    """One connection, one lock. Every SQL statement runs while the lock is held."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._closed = False
        if self.path.exists() and not _schema_migrations_exists(self.path):
            _backup_pre_engine(self.path)
        self._conn = sqlite3.connect(
            str(self.path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            for migration in load_migrations():
                migration.apply(self._conn)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.close()
            self._closed = True

    def __enter__(self) -> SqliteStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def schema_version(self) -> int:
        with self.locked() as conn:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            value = row[0] if row is not None else None
            return int(value) if value is not None else 0

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        *,
        tokens_per_sec: float | None = None,
        elapsed: float | None = None,
        created_at: datetime | None = None,
    ) -> int:
        now = (created_at or datetime.now()).isoformat()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, tokens_per_sec, "
                "elapsed, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, role, content, tokens_per_sec, elapsed, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            row_id = cur.lastrowid
        if row_id is None:
            raise sqlite3.Error("INSERT INTO messages did not produce lastrowid")
        return int(row_id)


def backup_path_for(db_path: str | Path) -> Path:
    path = Path(db_path)
    return path.parent / f"{path.name}{BACKUP_SUFFIX}"
