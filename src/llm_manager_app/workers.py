"""QThread adapters around ChatService and BackendRegistry."""

from __future__ import annotations

import threading

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QThread, Signal, Slot

from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import GenerationParams, ModelRef
from llm_engine.services.chat import ChatService
from llm_engine.services.session import ModelSession
from llm_engine.store.vault import MemoryVault

_BALANCED = GenerationParams.preset("balanced")


def _params(value: object) -> GenerationParams:
    return value if isinstance(value, GenerationParams) else _BALANCED


class ChatWorker(QObject):
    """Lives on a QThread. ChatService generation still runs on its own worker."""

    token = Signal(int, str)
    done = Signal(int, bool, int, float, float)
    error = Signal(int, str, str)
    load_progress = Signal(int)
    accepted = Signal(int, str)
    rejected = Signal(int, str, str)
    unloaded = Signal()
    unload_failed = Signal(str, str)
    catalog_loaded = Signal(object)
    catalog_unloaded = Signal()
    catalog_deleted = Signal()
    catalog_failed = Signal(str, str)
    memories_created = Signal(object)
    memories_failed = Signal(str, str)

    def __init__(self, chat: ChatService, session: ModelSession | None = None) -> None:
        super().__init__()
        self._chat = chat
        self._session = session
        chat.on_token = self._on_token
        chat.on_done = self._on_done
        chat.on_error = self._on_error
        chat.on_load_progress = self._on_load_progress

    @Slot(int, str, object, object)
    def send(
        self, conversation_id: int, content: str, params: object = None, cancel: object = None
    ) -> None:
        try:
            self._chat.send(
                conversation_id, content, _params(params),
                cancel=cancel if isinstance(cancel, threading.Event) else None,
            )
        except EngineError as exc:
            self.rejected.emit(conversation_id, exc.code, str(exc))
            return
        self.accepted.emit(conversation_id, "send")

    @Slot(int, object, object)
    def regenerate(
        self, conversation_id: int, params: object = None, cancel: object = None
    ) -> None:
        try:
            self._chat.regenerate(
                conversation_id, _params(params),
                cancel=cancel if isinstance(cancel, threading.Event) else None,
            )
        except EngineError as exc:
            self.rejected.emit(conversation_id, exc.code, str(exc))
            return
        self.accepted.emit(conversation_id, "regenerate")

    @Slot()
    def unload(self) -> None:
        session = self._session
        if session is None:
            self.unload_failed.emit("not_found", "no session")
            return
        try:
            dropped_or_idle = session.force_unload()
        except EngineError as exc:
            self.unload_failed.emit(exc.code, str(exc))
            return
        except Exception as exc:
            self.unload_failed.emit("backend_unavailable", str(exc))
            return
        if dropped_or_idle:
            self.unloaded.emit()
            return
        # Load still holds the lock with no handle yet; GUI must stay busy.
        self.unload_failed.emit("generating", "load still in progress")

    @Slot(object, object)
    def catalog_load(self, ref: object, cancel: object = None) -> None:
        if not isinstance(ref, ModelRef):
            self.catalog_failed.emit("config_invalid", "invalid model")
            return
        try:
            loaded = self._chat.catalog_load(
                ref, cancel=cancel if isinstance(cancel, threading.Event) else None
            )
        except EngineError as exc:
            self.catalog_failed.emit(exc.code, str(exc))
            return
        except Exception as exc:
            self.catalog_failed.emit("load_failed", str(exc))
            return
        self.catalog_loaded.emit(loaded)

    @Slot()
    def catalog_unload(self) -> None:
        try:
            self._chat.catalog_unload()
        except EngineError as exc:
            self.catalog_failed.emit(exc.code, str(exc))
            return
        except Exception as exc:
            self.catalog_failed.emit("backend_unavailable", str(exc))
            return
        self.catalog_unloaded.emit()

    @Slot(object)
    def catalog_delete(self, ref: object) -> None:
        try:
            if not isinstance(ref, ModelRef):
                raise EngineError("config_invalid", "Invalid model.")
            self._chat.catalog_delete(ref)
        except EngineError as exc:
            self.catalog_failed.emit(exc.code, str(exc))
        except Exception as exc:
            self.catalog_failed.emit("delete_failed", str(exc))
        else:
            self.catalog_deleted.emit()

    @Slot(int, object, object)
    def remember(self, conversation_id: int, vault: object, cancel: object) -> None:
        try:
            if not isinstance(vault, MemoryVault) or not isinstance(cancel, threading.Event):
                raise EngineError("config_invalid", "Invalid memory request.")
            result = self._chat.capture_memories(conversation_id, vault, cancel)
        except EngineError as exc:
            self.memories_failed.emit(exc.code, str(exc))
        except Exception as exc:
            self.memories_failed.emit("memory_failed", str(exc))
        else:
            self.memories_created.emit(result)

    def _on_token(self, conversation_id: int, text: str) -> None:
        QMetaObject.invokeMethod(
            self,
            "_emit_token",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, conversation_id),
            Q_ARG(str, text),
        )

    def _on_done(
        self,
        conversation_id: int,
        *,
        cancelled: bool,
        chunks: int,
        elapsed: float,
        tps: float,
    ) -> None:
        QMetaObject.invokeMethod(
            self,
            "_emit_done",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, conversation_id),
            Q_ARG(bool, cancelled),
            Q_ARG(int, chunks),
            Q_ARG(float, elapsed),
            Q_ARG(float, tps),
        )

    def _on_error(self, conversation_id: int, error: EngineError) -> None:
        QMetaObject.invokeMethod(
            self,
            "_emit_error",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, conversation_id),
            Q_ARG(str, error.code),
            Q_ARG(str, str(error)),
        )

    def _on_load_progress(self, conversation_id: int, *args: object, **kwargs: object) -> None:
        del args, kwargs
        QMetaObject.invokeMethod(
            self,
            "_emit_load_progress",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(int, conversation_id),
        )

    @Slot(int, str)
    def _emit_token(self, conversation_id: int, text: str) -> None:
        self.token.emit(conversation_id, text)

    @Slot(int, bool, int, float, float)
    def _emit_done(
        self,
        conversation_id: int,
        cancelled: bool,
        chunks: int,
        elapsed: float,
        tps: float,
    ) -> None:
        self.done.emit(conversation_id, cancelled, chunks, elapsed, tps)

    @Slot(int, str, str)
    def _emit_error(self, conversation_id: int, code: str, message: str) -> None:
        self.error.emit(conversation_id, code, message)

    @Slot(int)
    def _emit_load_progress(self, conversation_id: int) -> None:
        self.load_progress.emit(conversation_id)


def start_chat_worker(
    chat: ChatService,
    parent: QObject | None = None,
    *,
    session: ModelSession | None = None,
) -> tuple[QThread, ChatWorker]:
    thread = QThread(parent)
    worker = ChatWorker(chat, session=session)
    worker.moveToThread(thread)
    thread.start()
    return thread, worker


class CatalogWorker(QObject):
    """Lives on a QThread. ``registry.list_models`` must not run on the GUI thread."""

    listed = Signal(object, object)
    failed = Signal(str, str)

    def __init__(self, registry: BackendRegistry) -> None:
        super().__init__()
        self._registry = registry

    @Slot()
    def list_models(self) -> None:
        try:
            models, availability = self._registry.list_models()
        except EngineError as exc:
            self.failed.emit(exc.code, str(exc))
            return
        except Exception as exc:
            self.failed.emit("backend_unavailable", str(exc))
            return
        self.listed.emit(list(models), dict(availability))


def start_catalog_worker(
    registry: BackendRegistry, parent: QObject | None = None
) -> tuple[QThread, CatalogWorker]:
    thread = QThread(parent)
    worker = CatalogWorker(registry)
    worker.moveToThread(thread)
    thread.start()
    return thread, worker
