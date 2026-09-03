"""Streaming chat sessions: persist user, generate on a worker, persist assistant."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import ChatTurn, Conversation, GenerationParams, ModelRef
from llm_engine.logging import get_logger
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService

_log = get_logger("chat")

DEFAULT_TITLES = frozenset({"New Chat", "New chat", ""})
_COALESCE_SECONDS = 0.040
_COALESCE_CHUNKS = 32

OnToken = Callable[[int, str], object]
OnDone = Callable[..., object]
OnError = Callable[[int, EngineError], object]
OnLoadProgress = Callable[..., object]


def title_from(text: str) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    if len(line) > 42:
        line = line[:42].rstrip() + "…"
    return line or "New Chat"


def assemble_prompt(conversation: Conversation) -> list[ChatTurn]:
    turns: list[ChatTurn] = []
    prompt = conversation.system_prompt.strip()
    if prompt:
        turns.append(ChatTurn(role="system", content=prompt))
    for message in conversation.messages:
        if message.role in {"user", "assistant"}:
            turns.append(message)
    return turns


class ChatService:
    """One generation at a time. ``send`` / ``regenerate`` return after the user txn."""

    def __init__(
        self,
        library: LibraryService,
        session: ModelSession,
        *,
        on_token: OnToken | None = None,
        on_done: OnDone | None = None,
        on_error: OnError | None = None,
        on_load_progress: OnLoadProgress | None = None,
    ) -> None:
        self._library = library
        self._session = session
        self._store = library._store
        self.on_token = on_token
        self.on_done = on_done
        self.on_error = on_error
        self.on_load_progress = on_load_progress
        self._state_lock = threading.Lock()
        self._generating = False
        self._active_id: int | None = None
        self._cancel = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def send(
        self,
        conversation_id: int,
        content: str,
        params: GenerationParams | None = None,
    ) -> None:
        messages, model, cancel = self._accept(conversation_id, persist_user=content)
        self._start_worker(conversation_id, model, messages, params, cancel)

    def regenerate(
        self,
        conversation_id: int,
        params: GenerationParams | None = None,
    ) -> None:
        messages, model, cancel = self._accept(conversation_id, persist_user=None)
        self._start_worker(conversation_id, model, messages, params, cancel)

    def stop(self, conversation_id: int) -> None:
        with self._state_lock:
            if not self._generating or self._active_id != conversation_id:
                return
            self._cancel.set()
        _log.info("stop conversation=%s", conversation_id)

    def set_system_prompt(self, conversation_id: int, text: str) -> None:
        self._library._update_conversation(conversation_id, system_prompt=text)

    def set_model(self, conversation_id: int, ref: ModelRef) -> None:
        self._library._update_conversation(
            conversation_id,
            model_name=ref.name,
            backend=str(ref.backend),
        )

    def _accept(
        self,
        conversation_id: int,
        *,
        persist_user: str | None,
    ) -> tuple[list[ChatTurn], ModelRef, threading.Event]:
        with self._state_lock:
            if self._generating:
                raise EngineError("generating", "generation already in progress")
            if persist_user is not None and not persist_user.strip():
                raise EngineError("config_invalid", "empty content")
            conv = self._library.get_conversation(conversation_id)
            if conv.summary.model is None:
                raise EngineError("no_model", "conversation has no model")
            if persist_user is None and not conv.messages:
                raise EngineError("not_found", "no messages to regenerate")
            cancel = threading.Event()
            self._generating = True
            self._active_id = conversation_id
            self._cancel = cancel
        try:
            if persist_user is not None:
                self._persist_user(conv, persist_user)
                conv = self._library.get_conversation(conversation_id)
            elif conv.messages[-1].role == "assistant":
                self._delete_last_assistant(conversation_id)
                conv = self._library.get_conversation(conversation_id)
                if not conv.messages:
                    raise EngineError("not_found", "no messages to regenerate")
            model = conv.summary.model
            if model is None:
                raise EngineError("no_model", "conversation has no model")
            return assemble_prompt(conv), model, cancel
        except Exception:
            self._release(cancel)
            raise

    def _start_worker(
        self,
        conversation_id: int,
        model: ModelRef,
        messages: list[ChatTurn],
        params: GenerationParams | None,
        cancel: threading.Event,
    ) -> None:
        resolved = params if params is not None else GenerationParams()
        thread = threading.Thread(
            target=self._worker,
            args=(conversation_id, model, messages, resolved, cancel),
            name="chat-generate",
            daemon=True,
        )
        self._worker_thread = thread
        _log.info("send conversation=%s", conversation_id)
        thread.start()

    def _worker(
        self,
        conversation_id: int,
        model: ModelRef,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: threading.Event,
    ) -> None:
        pending: list[str] = []
        parts: list[str] = []
        chunks = 0
        last_flush = time.monotonic()
        started = time.monotonic()
        during_load = True
        error: EngineError | None = None
        try:
            self._ensure_loaded(model, conversation_id)
            during_load = False
            started = time.monotonic()
            for token in self._session.generate(messages, params, cancel):
                chunks += 1
                pending.append(token)
                parts.append(token)
                if len(pending) >= _COALESCE_CHUNKS or (
                    time.monotonic() - last_flush >= _COALESCE_SECONDS
                ):
                    self._flush_tokens(conversation_id, pending)
                    last_flush = time.monotonic()
        except Exception as exc:
            error = self._wrap_error(exc, during_load=during_load)
        elapsed = time.monotonic() - started
        tps = chunks / elapsed if elapsed > 0 else 0.0
        cancelled = error is None and cancel.is_set()
        self._flush_tokens(conversation_id, pending)
        text = "".join(parts)
        should_persist = chunks >= 1 or (error is None and not cancelled)
        if should_persist:
            try:
                self._store.add_message(
                    conversation_id,
                    "assistant",
                    text,
                    tokens_per_sec=tps,
                    elapsed=elapsed,
                )
            except Exception as exc:
                if error is None:
                    error = self._wrap_error(exc, during_load=False)
        self._release(cancel)
        if error is not None:
            self._emit_error(conversation_id, error)
            return
        self._emit_done(
            conversation_id,
            cancelled=cancelled,
            chunks=chunks,
            elapsed=elapsed,
            tps=tps,
        )

    def _ensure_loaded(self, model: ModelRef, conversation_id: int) -> None:
        progress = self.on_load_progress

        def _on_progress(*args: object, **kwargs: object) -> None:
            if progress is not None:
                progress(conversation_id, *args, **kwargs)

        self._session.load(model, on_progress=_on_progress if progress is not None else None)

    def _persist_user(self, conv: Conversation, content: str) -> None:
        now = datetime.now().isoformat()
        conversation_id = conv.summary.id
        new_title = title_from(content) if conv.summary.title in DEFAULT_TITLES else None
        with self._store.transaction() as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, tokens_per_sec, "
                "elapsed, created_at) VALUES (?, ?, ?, NULL, NULL, ?)",
                (conversation_id, "user", content, now),
            )
            if new_title is not None:
                conn.execute(
                    "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                    (new_title, now, conversation_id),
                )
            else:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (now, conversation_id),
                )

    def _delete_last_assistant(self, conversation_id: int) -> None:
        now = datetime.now().isoformat()
        with self._store.transaction() as conn:
            row = conn.execute(
                "SELECT id, role FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if row is None or row["role"] != "assistant":
                return
            conn.execute("DELETE FROM messages WHERE id = ?", (row["id"],))
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )

    def _flush_tokens(self, conversation_id: int, pending: list[str]) -> None:
        if not pending:
            return
        text = "".join(pending)
        pending.clear()
        callback = self.on_token
        if callback is not None:
            callback(conversation_id, text)

    def _emit_done(
        self,
        conversation_id: int,
        *,
        cancelled: bool,
        chunks: int,
        elapsed: float,
        tps: float,
    ) -> None:
        callback = self.on_done
        if callback is not None:
            callback(
                conversation_id,
                cancelled=cancelled,
                chunks=chunks,
                elapsed=elapsed,
                tps=tps,
            )

    def _emit_error(self, conversation_id: int, error: EngineError) -> None:
        _log.error("generation failed conversation=%s code=%s", conversation_id, error.code)
        callback = self.on_error
        if callback is not None:
            callback(conversation_id, error)

    def _release(self, cancel: threading.Event) -> None:
        with self._state_lock:
            if self._cancel is cancel:
                self._generating = False
                self._active_id = None

    @staticmethod
    def _wrap_error(exc: BaseException, *, during_load: bool) -> EngineError:
        code = "load_failed" if during_load else "backend_unavailable"
        if isinstance(exc, EngineError) and exc.code == code:
            return exc
        return EngineError(code, str(exc))
