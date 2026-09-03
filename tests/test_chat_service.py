from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    GenerationParams,
    LocalModel,
    ModelRef,
)
from llm_engine.services.chat import ChatService, title_from
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore

REF = ModelRef(BackendName.OLLAMA, "fake")
LOCAL = LocalModel(ref=REF, path=None, size_bytes=0)


class RecordingFake(FakeBackend):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.prompts: list[list[ChatTurn]] = []
        self.params: list[GenerationParams] = []

    def stream_generate(self, handle, messages, params, cancel):  # type: ignore[no-untyped-def]
        self.prompts.append(list(messages))
        self.params.append(params)
        yield from super().stream_generate(handle, messages, params, cancel)


class Recorder:
    def __init__(self) -> None:
        self.tokens: list[tuple[int, str]] = []
        self.dones: list[dict[str, object]] = []
        self.errors: list[EngineError] = []
        self.events: list[str] = []
        self.terminal = threading.Event()
        self._lock = threading.Lock()

    def on_token(self, conversation_id: int, text: str) -> None:
        with self._lock:
            self.tokens.append((conversation_id, text))
            self.events.append("token")

    def on_done(
        self,
        conversation_id: int,
        *,
        cancelled: bool,
        chunks: int,
        elapsed: float,
        tps: float,
    ) -> None:
        with self._lock:
            self.dones.append(
                {
                    "id": conversation_id,
                    "cancelled": cancelled,
                    "chunks": chunks,
                    "elapsed": elapsed,
                    "tps": tps,
                }
            )
            self.events.append("done")
        self.terminal.set()

    def on_error(self, conversation_id: int, error: EngineError) -> None:
        del conversation_id
        with self._lock:
            self.errors.append(error)
            self.events.append("error")
        self.terminal.set()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStore]:
    opened = SqliteStore(tmp_path / "data.db")
    yield opened
    opened.close()


@pytest.fixture
def library(store: SqliteStore) -> LibraryService:
    return LibraryService(store)


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timeout waiting for condition")


def _wait(rec: Recorder, chat: ChatService, timeout: float = 2.0) -> None:
    assert rec.terminal.wait(timeout), (
        f"timed out dones={rec.dones!r} errors={[e.code for e in rec.errors]!r}"
    )
    thread = chat._worker_thread
    if thread is not None:
        thread.join(timeout)


def _harness(
    library: LibraryService,
    *,
    chunks: Sequence[str] = ("Hello", " world"),
    fail_after: int | None = None,
    block_generate: threading.Event | None = None,
    block_load: threading.Event | None = None,
    models: Sequence[LocalModel] | None = None,
) -> tuple[ChatService, Recorder, RecordingFake, ModelSession]:
    fake = RecordingFake(
        models=list(models) if models is not None else [LOCAL],
        chunks=chunks,
        fail_after=fail_after,
        block_generate=block_generate,
        block_load=block_load,
    )
    session = ModelSession(BackendRegistry([fake]))
    rec = Recorder()
    chat = ChatService(
        library,
        session,
        on_token=rec.on_token,
        on_done=rec.on_done,
        on_error=rec.on_error,
    )
    return chat, rec, fake, session


def _new_chat(library: LibraryService, model: ModelRef = REF) -> int:
    return library.create_conversation(model=model).summary.id


def test_title_from_first_line_and_ellipsis() -> None:
    assert title_from("") == "New Chat"
    assert title_from("Hello\nWorld") == "Hello"
    assert title_from("x" * 42) == "x" * 42
    assert title_from("x" * 43) == "x" * 42 + "…"


def test_system_prompt_not_stored_as_a_message(library: LibraryService) -> None:
    chat, rec, fake, _session = _harness(library)
    cid = _new_chat(library)
    chat.set_system_prompt(cid, "You are terse.")
    chat.send(cid, "Hi there")
    _wait(rec, chat)
    loaded = library.get_conversation(cid)
    assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
    assert all(turn.role != "system" for turn in loaded.messages)
    assert fake.prompts
    assert fake.prompts[0][0] == ChatTurn(role="system", content="You are terse.")
    assert fake.prompts[0][1].role == "user"
    assert fake.prompts[0][1].content == "Hi there"


def test_project_seed_used_at_generate_time(library: LibraryService) -> None:
    project = library.create_project(
        "Coding",
        instructions="Stay on rails.",
        model=REF,
    )
    seeded = library.create_conversation(project_id=project.id)
    chat, rec, fake, _session = _harness(library)
    chat.send(seeded.summary.id, "go")
    _wait(rec, chat)
    loaded = library.get_conversation(seeded.summary.id)
    assert loaded.system_prompt == "Stay on rails."
    assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
    assert fake.prompts[0][0] == ChatTurn(role="system", content="Stay on rails.")


def test_retitle_only_on_default_titles(library: LibraryService) -> None:
    chat, rec, _fake, _session = _harness(library)
    cid = _new_chat(library)
    chat.send(cid, "First line\nignored")
    _wait(rec, chat)
    assert library.get_conversation(cid).summary.title == "First line"

    rec.terminal.clear()
    library.rename(cid, "Keep me")
    chat.send(cid, "should not retitle")
    _wait(rec, chat)
    assert library.get_conversation(cid).summary.title == "Keep me"

    rec.terminal.clear()
    other = _new_chat(library)
    library.rename(other, "New chat")
    chat.send(other, "From alt default")
    _wait(rec, chat)
    assert library.get_conversation(other).summary.title == "From alt default"

    rec.terminal.clear()
    blank = _new_chat(library)
    library.rename(blank, "")
    chat.send(blank, "From empty title")
    _wait(rec, chat)
    assert library.get_conversation(blank).summary.title == "From empty title"


def test_send_rejects_empty_generating_and_no_model(library: LibraryService) -> None:
    gate = threading.Event()
    chat, rec, _fake, _session = _harness(library, block_generate=gate)
    cid = _new_chat(library)
    with pytest.raises(EngineError) as empty:
        chat.send(cid, "   \n")
    assert empty.value.code == "config_invalid"
    untitled = library.create_conversation()
    with pytest.raises(EngineError) as missing:
        chat.send(untitled.summary.id, "hello")
    assert missing.value.code == "no_model"
    chat.send(cid, "hello")
    with pytest.raises(EngineError) as busy:
        chat.send(cid, "again")
    assert busy.value.code == "generating"
    with pytest.raises(EngineError) as regen_busy:
        chat.regenerate(cid)
    assert regen_busy.value.code == "generating"
    gate.set()
    _wait(rec, chat)


def test_stop_before_token(library: LibraryService) -> None:
    gate = threading.Event()
    chat, rec, fake, _session = _harness(library, chunks=("Hello",), block_load=gate)
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait_until(lambda: len(fake.load_calls) > 0)
    chat.stop(cid)
    gate.set()
    _wait(rec, chat)
    assert rec.dones
    assert rec.dones[0]["cancelled"] is True
    assert rec.dones[0]["chunks"] == 0
    assert rec.errors == []
    loaded = library.get_conversation(cid)
    assert [turn.role for turn in loaded.messages] == ["user"]


def test_stop_after_token_persists_partial(library: LibraryService) -> None:
    gate = threading.Event()
    chat, rec, _fake, session = _harness(
        library, chunks=("Hello", " world"), block_generate=gate
    )
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait_until(lambda: session.status().generating)
    chat.stop(cid)
    gate.set()
    _wait(rec, chat)
    assert rec.dones[0]["cancelled"] is True
    assert rec.dones[0]["chunks"] >= 1
    assert rec.errors == []
    loaded = library.get_conversation(cid)
    assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[-1].content.startswith("Hello")


def test_stop_idle_is_noop(library: LibraryService) -> None:
    chat, rec, _fake, _session = _harness(library)
    cid = _new_chat(library)
    chat.stop(cid)
    assert rec.dones == []
    assert rec.errors == []


def test_regenerate_edges(library: LibraryService) -> None:
    chat, rec, fake, _session = _harness(library, chunks=("one",))
    empty = _new_chat(library)
    with pytest.raises(EngineError) as missing:
        chat.regenerate(empty)
    assert missing.value.code == "not_found"

    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait(rec, chat)
    assert [turn.role for turn in library.get_conversation(cid).messages] == [
        "user",
        "assistant",
    ]
    rec.terminal.clear()
    fake.chunks = ("two",)
    chat.regenerate(cid)
    _wait(rec, chat)
    loaded = library.get_conversation(cid)
    assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[-1].content == "two"
    assert rec.dones[-1]["cancelled"] is False

    rec.terminal.clear()
    failing = RecordingFake(models=[LOCAL], chunks=("x",), fail_after=0)
    session = ModelSession(BackendRegistry([failing]))
    rec2 = Recorder()
    broken = ChatService(
        library,
        session,
        on_token=rec2.on_token,
        on_done=rec2.on_done,
        on_error=rec2.on_error,
    )
    other = _new_chat(library)
    broken.send(other, "hello")
    _wait(rec2, broken)
    assert rec2.errors
    assert [turn.role for turn in library.get_conversation(other).messages] == ["user"]
    rec2.terminal.clear()
    rec2.errors.clear()
    rec2.events.clear()
    failing._fail_after = None
    failing.chunks = ("recovered",)
    broken.regenerate(other)
    _wait(rec2, broken)
    loaded = library.get_conversation(other)
    assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[-1].content == "recovered"


def test_yield_once_then_raise_persists_partial_no_done(library: LibraryService) -> None:
    chat, rec, _fake, _session = _harness(library, chunks=("partial", "more"), fail_after=1)
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait(rec, chat)
    assert rec.errors
    assert rec.errors[0].code == "backend_unavailable"
    assert "done" not in rec.events
    assert rec.events[-1] == "error"
    loaded = library.get_conversation(cid)
    assert [turn.role for turn in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[-1].content == "partial"


def test_raise_before_yield_no_assistant_row(library: LibraryService) -> None:
    chat, rec, _fake, _session = _harness(library, fail_after=0)
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait(rec, chat)
    assert rec.errors
    assert rec.errors[0].code == "backend_unavailable"
    assert rec.dones == []
    loaded = library.get_conversation(cid)
    assert [turn.role for turn in loaded.messages] == ["user"]


def test_unflushed_chunks_flushed_before_error(library: LibraryService) -> None:
    pieces = tuple(chr(97 + i) for i in range(31))
    chat, rec, _fake, _session = _harness(library, chunks=pieces, fail_after=31)
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait(rec, chat)
    expected = "".join(pieces)
    assert "".join(text for _cid, text in rec.tokens) == expected
    assert rec.events[0] == "token"
    assert rec.events[-1] == "error"
    assert "done" not in rec.events
    loaded = library.get_conversation(cid)
    assert loaded.messages[-1].role == "assistant"
    assert loaded.messages[-1].content == expected


def test_list_conversations_during_blocked_generate(library: LibraryService) -> None:
    gate = threading.Event()
    chat, rec, _fake, session = _harness(library, block_generate=gate)
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait_until(lambda: session.status().generating)
    time.sleep(0.05)
    rows = library.list_conversations()
    assert len(rows) == 1
    assert rows[0].id == cid
    gate.set()
    _wait(rec, chat)
    assert rec.dones
    assert rec.errors == []


def test_set_model_and_system_prompt_during_generate(library: LibraryService) -> None:
    other = ModelRef(BackendName.OLLAMA, "other")
    gate = threading.Event()
    chat, rec, fake, session = _harness(
        library,
        block_generate=gate,
        models=[LOCAL, LocalModel(ref=other, path=None, size_bytes=0)],
    )
    cid = _new_chat(library)
    chat.set_system_prompt(cid, "first")
    chat.send(cid, "hello")
    _wait_until(lambda: session.status().generating)
    chat.set_system_prompt(cid, "next turn")
    chat.set_model(cid, other)
    gate.set()
    _wait(rec, chat)
    assert fake.prompts[0][0].content == "first"
    loaded = library.get_conversation(cid)
    assert loaded.system_prompt == "next turn"
    assert loaded.summary.model == other


def test_send_returns_before_generation_finishes(library: LibraryService) -> None:
    gate = threading.Event()
    chat, rec, _fake, _session = _harness(library, block_generate=gate)
    cid = _new_chat(library)
    started = time.monotonic()
    chat.send(cid, "hello")
    assert time.monotonic() - started < 0.5
    assert not rec.terminal.is_set()
    gate.set()
    _wait(rec, chat)
    assert rec.dones[0]["cancelled"] is False
    assert rec.dones[0]["chunks"] == 2
    loaded = library.get_conversation(cid)
    assert loaded.messages[-1].content == "Hello world"


def test_flush_callback_error_releases_lock_and_emits_error(library: LibraryService) -> None:
    rec = Recorder()

    def boom(conversation_id: int, text: str) -> None:
        rec.on_token(conversation_id, text)
        raise RuntimeError("flush failed")

    fake = RecordingFake(models=[LOCAL], chunks=("Hello", " world"))
    session = ModelSession(BackendRegistry([fake]))
    chat = ChatService(
        library,
        session,
        on_token=boom,
        on_done=rec.on_done,
        on_error=rec.on_error,
    )
    cid = _new_chat(library)
    chat.send(cid, "hello")
    _wait(rec, chat)
    assert rec.errors
    assert rec.errors[0].code == "backend_unavailable"
    assert rec.dones == []
    assert rec.events[0] == "token"
    assert rec.events[-1] == "error"
    assert session.status().generating is False
    loaded = library.get_conversation(cid)
    assert loaded.messages[-1].role == "assistant"
    assert loaded.messages[-1].content == "Hello world"

    rec.terminal.clear()
    rec.errors.clear()
    rec.dones.clear()
    rec.events.clear()
    rec.tokens.clear()
    chat.on_token = rec.on_token
    chat.send(cid, "again")
    _wait(rec, chat)
    assert rec.dones
    assert rec.errors == []


def test_load_progress_fires_with_conversation_id(library: LibraryService) -> None:
    gate = threading.Event()
    rec = Recorder()
    progress: list[tuple[int, tuple[object, ...]]] = []
    seen = threading.Event()

    def on_progress(conversation_id: int, *args: object, **kwargs: object) -> None:
        del kwargs
        progress.append((conversation_id, args))
        seen.set()

    fake = RecordingFake(models=[LOCAL], chunks=("Hello",), block_load=gate)
    session = ModelSession(BackendRegistry([fake]))
    chat = ChatService(
        library,
        session,
        on_token=rec.on_token,
        on_done=rec.on_done,
        on_error=rec.on_error,
        on_load_progress=on_progress,
    )
    cid = _new_chat(library)
    chat.send(cid, "hello")
    assert seen.wait(timeout=2)
    assert progress
    assert progress[0][0] == cid
    assert progress[0][1] == (0.0,)
    _wait_until(lambda: len(fake.load_calls) > 0)
    gate.set()
    _wait(rec, chat)
    assert rec.dones
    assert (cid, (1.0,)) in progress


def test_success_coalesces_tokens_and_never_emits_error(library: LibraryService) -> None:
    chat, rec, fake, _session = _harness(library)
    cid = _new_chat(library)
    chat.send(cid, "hello", GenerationParams.preset("precise"))
    _wait(rec, chat)
    assert "".join(text for _cid, text in rec.tokens) == "Hello world"
    assert rec.errors == []
    assert rec.events[-1] == "done"
    assert fake.params[0] == GenerationParams.preset("precise")
    done = rec.dones[0]
    assert done["cancelled"] is False
    assert done["chunks"] == 2
    assert isinstance(done["elapsed"], float)
    assert isinstance(done["tps"], float)
