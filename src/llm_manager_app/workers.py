"""QThread adapter around ChatService. Callbacks hop to Qt via queued signals."""

from __future__ import annotations

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QThread, Signal, Slot

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import GenerationParams
from llm_engine.services.chat import ChatService
from llm_engine.services.session import ModelSession

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

    def __init__(self, chat: ChatService, session: ModelSession | None = None) -> None:
        super().__init__()
        self._chat = chat
        self._session = session
        chat.on_token = self._on_token
        chat.on_done = self._on_done
        chat.on_error = self._on_error
        chat.on_load_progress = self._on_load_progress

    @Slot(int, str, object)
    def send(self, conversation_id: int, content: str, params: object = None) -> None:
        try:
            self._chat.send(conversation_id, content, _params(params))
        except EngineError as exc:
            self.rejected.emit(conversation_id, exc.code, str(exc))
            return
        self.accepted.emit(conversation_id, "send")

    @Slot(int, object)
    def regenerate(self, conversation_id: int, params: object = None) -> None:
        try:
            self._chat.regenerate(conversation_id, _params(params))
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
            session.unload()
        except EngineError as exc:
            self.unload_failed.emit(exc.code, str(exc))
            return
        except Exception as exc:
            self.unload_failed.emit("backend_unavailable", str(exc))
            return
        self.unloaded.emit()

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
