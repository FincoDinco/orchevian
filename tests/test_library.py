from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore

SECRET = "UNIQUE_MESSAGE_BODY_SHOULD_NOT_APPEAR_IN_SUMMARIES"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStore]:
    db = tmp_path / "data.db"
    opened = SqliteStore(db)
    yield opened
    opened.close()


@pytest.fixture
def library(store: SqliteStore) -> LibraryService:
    return LibraryService(store)


def test_pragmas_wal_foreign_keys_busy_timeout(store: SqliteStore) -> None:
    with store.locked() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_list_conversations_returns_summaries_only(
    library: LibraryService, store: SqliteStore
) -> None:
    created = library.create_conversation()
    store.add_message(created.summary.id, "user", SECRET)
    store.add_message(created.summary.id, "assistant", "ok")
    summaries = library.list_conversations()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.id == created.summary.id
    assert summary.message_count == 2
    assert not hasattr(summary, "messages")
    assert SECRET not in repr(summary)
    assert SECRET not in summary.title


def test_get_conversation_loads_one_chat_messages(
    library: LibraryService, store: SqliteStore
) -> None:
    first = library.create_conversation()
    second = library.create_conversation()
    store.add_message(first.summary.id, "user", "alpha")
    store.add_message(second.summary.id, "user", "beta")
    store.add_message(second.summary.id, "assistant", "gamma")
    loaded = library.get_conversation(second.summary.id)
    assert loaded.summary.id == second.summary.id
    assert loaded.summary.message_count == 2
    assert [turn.content for turn in loaded.messages] == ["beta", "gamma"]
    other = library.get_conversation(first.summary.id)
    assert [turn.content for turn in other.messages] == ["alpha"]


def test_get_conversation_missing_raises(library: LibraryService) -> None:
    with pytest.raises(EngineError) as exc:
        library.get_conversation(999)
    assert exc.value.code == "not_found"


def test_create_rename_delete_conversation(library: LibraryService) -> None:
    created = library.create_conversation()
    assert created.summary.title == "New Chat"
    assert created.messages == ()
    library.rename(created.summary.id, "Renamed")
    loaded = library.get_conversation(created.summary.id)
    assert loaded.summary.title == "Renamed"
    library.delete_conversation(created.summary.id)
    with pytest.raises(EngineError) as exc:
        library.get_conversation(created.summary.id)
    assert exc.value.code == "not_found"


def test_delete_conversation_cascades_messages(library: LibraryService, store: SqliteStore) -> None:
    created = library.create_conversation()
    store.add_message(created.summary.id, "user", "bye")
    library.delete_conversation(created.summary.id)
    with store.locked() as conn:
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert count == 0


def test_search_conversations_by_title(library: LibraryService) -> None:
    a = library.create_conversation()
    b = library.create_conversation()
    library.rename(a.summary.id, "Alpha notes")
    library.rename(b.summary.id, "Beta work")
    found = library.list_conversations(query="alpha")
    assert [row.id for row in found] == [a.summary.id]


def test_list_conversations_filters_project(library: LibraryService) -> None:
    project = library.create_project("Work")
    assigned = library.create_conversation(project_id=project.id)
    unassigned = library.create_conversation()
    in_project = library.list_conversations(project_id=project.id)
    assert [row.id for row in in_project] == [assigned.summary.id]
    none = library.list_conversations(project_id=None)
    assert [row.id for row in none] == [unassigned.summary.id]
    all_rows = library.list_conversations()
    assert {row.id for row in all_rows} == {assigned.summary.id, unassigned.summary.id}


def test_project_seed_copies_instructions_and_model(library: LibraryService) -> None:
    model = ModelRef(BackendName.OLLAMA, "qwen3:8b")
    project = library.create_project(
        "Coding",
        instructions="You are terse.",
        model=model,
    )
    seeded = library.create_conversation(project_id=project.id)
    assert seeded.system_prompt == "You are terse."
    assert seeded.summary.model == model
    assert seeded.summary.project_id == project.id


def test_project_seed_model_override(library: LibraryService) -> None:
    project = library.create_project(
        "Coding",
        instructions="stay on rails",
        model=ModelRef(BackendName.OLLAMA, "qwen3:8b"),
    )
    override = ModelRef(BackendName.GGUF, "tinyllama")
    seeded = library.create_conversation(project_id=project.id, model=override)
    assert seeded.system_prompt == "stay on rails"
    assert seeded.summary.model == override


def test_create_conversation_missing_project(library: LibraryService) -> None:
    with pytest.raises(EngineError) as exc:
        library.create_conversation(project_id=404)
    assert exc.value.code == "not_found"


def test_move_conversation(library: LibraryService) -> None:
    project = library.create_project("Work")
    chat = library.create_conversation()
    library.move(chat.summary.id, project.id)
    assert library.get_conversation(chat.summary.id).summary.project_id == project.id
    library.move(chat.summary.id, None)
    assert library.get_conversation(chat.summary.id).summary.project_id is None


def test_delete_project_sets_null_project_id(library: LibraryService) -> None:
    project = library.create_project("Temp")
    chat = library.create_conversation(project_id=project.id)
    library.delete_project(project.id)
    loaded = library.get_conversation(chat.summary.id)
    assert loaded.summary.project_id is None
    assert library.list_projects() == []


def test_update_project(library: LibraryService) -> None:
    project = library.create_project("Old")
    updated = library.update_project(
        project.id,
        name="New",
        instructions="do this",
        model=ModelRef(BackendName.MLX, "qwen"),
    )
    assert updated.name == "New"
    assert updated.instructions == "do this"
    assert updated.default_model == ModelRef(BackendName.MLX, "qwen")


def test_project_crud_not_found(library: LibraryService) -> None:
    with pytest.raises(EngineError) as exc:
        library.delete_project(1)
    assert exc.value.code == "not_found"


def test_list_from_other_thread(library: LibraryService) -> None:
    library.create_conversation()
    errors: list[Exception] = []

    def reader() -> None:
        try:
            rows = library.list_conversations()
            assert len(rows) == 1
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert errors == []


def test_busy_timeout_waits_for_second_writer(tmp_path: Path) -> None:
    db = tmp_path / "data.db"
    store = SqliteStore(db)
    library = LibraryService(store)
    library.create_conversation()

    blocker = sqlite3.connect(str(db), isolation_level=None, check_same_thread=False)
    blocker.execute("BEGIN EXCLUSIVE")
    blocker.execute("SELECT 1 FROM conversations").fetchone()

    started = threading.Event()
    finished = threading.Event()
    errors: list[Exception] = []

    def writer() -> None:
        started.set()
        try:
            library.create_conversation()
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(timeout=2)
    time.sleep(0.3)
    assert not finished.is_set()
    blocker.execute("COMMIT")
    thread.join(timeout=5)
    assert finished.is_set()
    assert errors == []
    assert len(library.list_conversations()) == 2
    blocker.close()
    store.close()


def test_templates_and_favorites_tables_exist(store: SqliteStore) -> None:
    with store.locked() as conn:
        names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"templates", "favorites", "projects", "conversations", "messages"} <= names
