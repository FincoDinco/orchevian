"""QThread adapter around ChatService. Callbacks hop to Qt via queued signals."""

from __future__ import annotations

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, QThread, Signal, Slot

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import GenerationParams
from llm_engine.services.chat import ChatService

_BALANCED = GenerationParams.preset("balanced")


class ChatWorker(QObject):
    """Lives on a QThread. ChatService generation still runs on its own worker."""

    token = Signal(int, str)
    done = Signal(int, bool, int, float, float)
    error = Signal(int, str, str)
    load_progress = Signal(int)
    accepted = Signal(int, str)
    rejected = Signal(int, str, str)

    def __init__(self, chat: ChatService) -> None:
        super().__init__()
        self._chat = chat
        chat.on_token = self._on_token
        chat.on_done = self._on_done
        chat.on_error = self._on_error
        chat.on_load_progress = self._on_load_progress

    @Slot(int, str)
    def send(self, conversation_id: int, content: str) -> None:
        try:
            self._chat.send(conversation_id, content, _BALANCED)
        except EngineError as exc:
            self.rejected.emit(conversation_id, exc.code, str(exc))
            return
        self.accepted.emit(conversation_id, "send")

    @Slot(int)
    def regenerate(self, conversation_id: int) -> None:
        try:
            self._chat.regenerate(conversation_id, _BALANCED)
        except EngineError as exc:
            self.rejected.emit(conversation_id, exc.code, str(exc))
            return
        self.accepted.emit(conversation_id, "regenerate")

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
    chat: ChatService, parent: QObject | None = None
) -> tuple[QThread, ChatWorker]:
    thread = QThread(parent)
    worker = ChatWorker(chat)
    worker.moveToThread(thread)
    thread.start()
    return thread, worker
