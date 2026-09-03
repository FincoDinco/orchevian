"""Column 3 chat: transcript, composer, stop, regenerate."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ChatTurn, Conversation
from llm_manager_app.widgets.composer import Composer
from llm_manager_app.widgets.transcript import Transcript


class ChatView(QWidget):
    send_requested = Signal(int, str)
    stop_requested = Signal(int)
    regenerate_requested = Signal(int)
    turn_finished = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPane")
        self._cid: int | None = None
        self._generating_id: int | None = None
        self._buffer = ""
        self._pending: tuple[str, int, str] | None = None
        self._error_plain_id: int | None = None
        self._undo_assistant: ChatTurn | None = None

        self._empty = QLabel("Select a conversation.", self)
        self._empty.setObjectName("chatEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        self._banner = QLabel(self)
        self._banner.setObjectName("chatBanner")
        self._banner.setWordWrap(True)
        self._banner.hide()

        self._regen = QPushButton("Regenerate", self)
        self._regen.setObjectName("regenerateButton")
        self._regen.setCursor(Qt.CursorShape.PointingHandCursor)
        self._regen.setEnabled(False)
        self._regen.clicked.connect(self.regenerate)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addStretch(1)
        toolbar.addWidget(self._regen)

        self._transcript = Transcript(self)
        self._composer = Composer(self)
        self._composer.send_requested.connect(self._on_send)
        self._composer.setEnabled(False)

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(8)
        body_layout.addWidget(self._banner)
        body_layout.addLayout(toolbar)
        body_layout.addWidget(self._transcript, 1)
        body_layout.addWidget(self._composer, 0)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    def conversation_id(self) -> int | None:
        return self._cid

    def is_streaming(self, cid: int | None = None) -> bool:
        if self._generating_id is None:
            return False
        return cid is None or cid == self._generating_id

    def keeping_error_buffer(self, cid: int | None) -> bool:
        return cid is not None and self._error_plain_id == cid and self._transcript.is_plain()

    def transcript(self) -> Transcript:
        return self._transcript

    def composer(self) -> Composer:
        return self._composer

    def banner_text(self) -> str:
        return self._banner.text() if self._banner.isVisible() else ""

    def set_conversation(self, conversation: Conversation | None) -> None:
        if conversation is None:
            self._cid = None
            self._pending = None
            self._error_plain_id = None
            self._undo_assistant = None
            self._transcript.clear()
            self._banner.hide()
            self._composer.clear()
            self._composer.setEnabled(False)
            self._regen.setEnabled(False)
            self._stack.setCurrentWidget(self._empty)
            return
        cid = conversation.summary.id
        if self.is_streaming(cid):
            self._cid = cid
            self._stack.setCurrentIndex(1)
            self._transcript.set_turns(conversation.messages)
            self._transcript.restore_stream(self._buffer)
            self._sync_enabled()
            return
        if self.keeping_error_buffer(cid) and self._cid == cid:
            self._stack.setCurrentIndex(1)
            return
        self._cid = cid
        self._error_plain_id = None
        self._stack.setCurrentIndex(1)
        self._banner.hide()
        self._transcript.set_turns(conversation.messages)
        self._sync_enabled()

    def focus_composer(self) -> None:
        if self._cid is None:
            return
        self._composer.focus_edit()

    def stop(self) -> None:
        cid = self._generating_id
        if cid is None:
            return
        self.stop_requested.emit(cid)

    def regenerate(self) -> None:
        cid = self._cid
        if cid is None or self._generating_id is not None or self._pending is not None:
            return
        if not self._transcript.turns():
            return
        self._pending = ("regenerate", cid, "")
        self._generating_id = cid
        self._buffer = ""
        self._banner.hide()
        self._error_plain_id = None
        self._undo_assistant = self._transcript.drop_last_assistant()
        self._transcript.begin_stream()
        self._sync_enabled()
        self.regenerate_requested.emit(cid)

    def on_accepted(self, conversation_id: int, kind: str) -> None:
        del kind
        # generating_id / stream already started on send; done may beat accepted.
        if self._pending is not None and self._pending[1] == conversation_id:
            self._pending = None
        self._undo_assistant = None
        self._sync_enabled()

    def on_rejected(self, conversation_id: int, code: str, message: str) -> None:
        pending = self._pending
        self._pending = None
        if self._generating_id == conversation_id:
            self._generating_id = None
        if pending is not None and pending[1] == conversation_id:
            if pending[0] == "send":
                self._composer.set_text(pending[2])
            if self._cid == conversation_id:
                self._transcript.revert_stream(
                    restore_user=pending[0] == "send",
                    restore=self._undo_assistant if pending[0] == "regenerate" else None,
                )
        self._undo_assistant = None
        self._buffer = ""
        if self._cid != conversation_id:
            self._sync_enabled()
            return
        self._banner.setText(message or code)
        self._banner.show()
        self._sync_enabled()
        self._composer.focus_edit()

    def on_token(self, conversation_id: int, text: str) -> None:
        if conversation_id != self._generating_id:
            return
        self._buffer += text
        if self._cid == conversation_id:
            self._transcript.append_stream(text)

    def on_done(
        self,
        conversation_id: int,
        cancelled: bool,
        chunks: int,
        elapsed: float,
        tps: float,
    ) -> None:
        del cancelled, chunks, elapsed, tps
        if conversation_id != self._generating_id:
            return
        self._generating_id = None
        if self._cid == conversation_id:
            self._transcript.finish_stream(parse_markdown=True)
        self._buffer = ""
        self._error_plain_id = None
        self._sync_enabled()
        self._composer.focus_edit()
        self.turn_finished.emit(conversation_id)

    def on_error(self, conversation_id: int, code: str, message: str) -> None:
        if conversation_id != self._generating_id:
            return
        self._generating_id = None
        if self._cid == conversation_id:
            self._transcript.keep_stream()
            self._error_plain_id = conversation_id
            self._banner.setText(message or code)
            self._banner.show()
            self._composer.focus_edit()
        self._sync_enabled()
        self.turn_finished.emit(conversation_id)

    def _on_send(self, text: str) -> None:
        cid = self._cid
        if cid is None or self._generating_id is not None or self._pending is not None:
            return
        if not text.strip():
            return
        self._pending = ("send", cid, text)
        self._generating_id = cid
        self._buffer = ""
        self._composer.clear()
        self._banner.hide()
        self._error_plain_id = None
        self._transcript.append_user(text)
        self._transcript.begin_stream()
        self._sync_enabled()
        self.send_requested.emit(cid, text)

    def _sync_enabled(self) -> None:
        has = self._cid is not None
        busy = self._generating_id is not None or self._pending is not None
        self._composer.setEnabled(has and not busy)
        self._regen.setEnabled(has and bool(self._transcript.turns()) and not busy)
