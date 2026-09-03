"""Column 3 chat: transcript, composer, inspector, stop, regenerate."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ChatTurn, Conversation, GenerationParams
from llm_manager_app.widgets.composer import Composer
from llm_manager_app.widgets.inspector import Inspector
from llm_manager_app.widgets.transcript import Transcript


class ChatView(QWidget):
    send_requested = Signal(int, str, object)
    stop_requested = Signal(int)
    regenerate_requested = Signal(int, object)
    turn_finished = Signal(int)
    system_prompt_changed = Signal(int, str)
    unload_requested = Signal()
    restart_requested = Signal()
    inspector_open_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("detailPane")
        self._cid: int | None = None
        self._generating_id: int | None = None
        self._buffer = ""
        self._pending: tuple[str, int, str] | None = None
        self._error_plain_id: int | None = None
        self._undo_assistant: ChatTurn | None = None
        self._rejected_drafts: dict[int, str] = {}
        self._rejected_banners: dict[int, str] = {}

        self._empty = QLabel("Select a conversation.", self)
        self._empty.setObjectName("chatEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)

        self._banner = QLabel(self)
        self._banner.setObjectName("chatBanner")
        self._banner.setWordWrap(True)
        self._banner.hide()

        self._inspector_btn = QPushButton("Inspector", self)
        self._inspector_btn.setObjectName("inspectorToggle")
        self._inspector_btn.setCheckable(True)
        self._inspector_btn.setChecked(True)
        self._inspector_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._inspector_btn.toggled.connect(self._on_inspector_toggled)

        self._regen = QPushButton("Regenerate", self)
        self._regen.setObjectName("regenerateButton")
        self._regen.setCursor(Qt.CursorShape.PointingHandCursor)
        self._regen.setEnabled(False)
        self._regen.clicked.connect(self.regenerate)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(self._inspector_btn)
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

        self._inspector = Inspector(self)
        self._inspector.system_prompt_changed.connect(self._on_system_prompt)
        self._inspector.unload_requested.connect(self._on_inspector_unload)
        self._inspector.restart_requested.connect(self._on_inspector_restart)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setObjectName("chatSplitter")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.addWidget(body)
        split.addWidget(self._inspector)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([620, 240])
        self._chat_split = split

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(split)

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

    def inspector(self) -> Inspector:
        return self._inspector

    def inspector_open(self) -> bool:
        return self._inspector.isVisible()

    def set_inspector_open(self, visible: bool) -> None:
        blocked = self._inspector_btn.blockSignals(True)
        self._inspector_btn.setChecked(visible)
        self._inspector_btn.blockSignals(blocked)
        self._inspector.setVisible(visible)
        if visible:
            sizes = self._chat_split.sizes()
            if len(sizes) == 2 and sizes[1] == 0:
                total = sum(sizes) or 860
                self._chat_split.setSizes([max(1, total - 240), 240])
        else:
            left = sum(self._chat_split.sizes()) or 1
            self._chat_split.setSizes([left, 0])

    def set_return_sends(self, enabled: bool) -> None:
        self._composer.set_return_sends(enabled)

    def generation_params(self) -> GenerationParams:
        return self._inspector.params()

    def banner_text(self) -> str:
        return self._banner.text() if self._banner.isVisible() else ""

    def show_banner(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.show()

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
            self._inspector.set_conversation(None)
            self._stack.setCurrentWidget(self._empty)
            return
        cid = conversation.summary.id
        if self.is_streaming(cid):
            self._cid = cid
            self._stack.setCurrentWidget(self._chat_split)
            self._transcript.set_turns(conversation.messages)
            self._transcript.restore_stream(self._buffer)
            self._inspector.set_conversation(conversation)
            self._inspector.set_generating(True)
            self._sync_enabled()
            return
        if self.keeping_error_buffer(cid) and self._cid == cid:
            self._stack.setCurrentWidget(self._chat_split)
            return
        self._cid = cid
        self._error_plain_id = None
        self._stack.setCurrentWidget(self._chat_split)
        self._transcript.set_turns(conversation.messages)
        self._inspector.set_conversation(conversation)
        if self._generating_id == cid:
            self._inspector.set_generating(True)
        draft = self._rejected_drafts.pop(cid, None)
        if draft is not None:
            self._composer.set_text(draft)
        banner = self._rejected_banners.pop(cid, None)
        if banner:
            self._banner.setText(banner)
            self._banner.show()
        else:
            self._banner.hide()
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
        self._inspector.flush_prompt()
        self._pending = ("regenerate", cid, "")
        self._generating_id = cid
        self._buffer = ""
        self._banner.hide()
        self._error_plain_id = None
        self._undo_assistant = self._transcript.drop_last_assistant()
        self._transcript.begin_stream()
        self._inspector.set_generating(True)
        self._sync_enabled()
        self.regenerate_requested.emit(cid, self._inspector.params())

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
            self._inspector.set_generating(False)
        draft: str | None = None
        restore_user = False
        restore_turn: ChatTurn | None = None
        if pending is not None and pending[1] == conversation_id:
            if pending[0] == "send":
                draft = pending[2]
                restore_user = True
            elif pending[0] == "regenerate":
                restore_turn = self._undo_assistant
        self._undo_assistant = None
        self._buffer = ""
        banner = message or code
        if self._cid != conversation_id:
            if draft is not None:
                self._rejected_drafts[conversation_id] = draft
            self._rejected_banners[conversation_id] = banner
            self._sync_enabled()
            return
        if restore_user or restore_turn is not None:
            self._transcript.revert_stream(restore_user=restore_user, restore=restore_turn)
        if draft is not None:
            self._composer.set_text(draft)
        self._banner.setText(banner)
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
        del cancelled
        if conversation_id != self._generating_id:
            return
        self._generating_id = None
        self._inspector.set_generating(False)
        self._inspector.set_last_turn(chunks=chunks, elapsed=elapsed, tps=tps)
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
        self._inspector.set_generating(False)
        if self._cid == conversation_id:
            self._transcript.keep_stream()
            self._error_plain_id = conversation_id
            self._banner.setText(message or code)
            self._banner.show()
            self._composer.focus_edit()
        self._sync_enabled()
        self.turn_finished.emit(conversation_id)

    def on_unloaded(self) -> None:
        cid = self._generating_id
        self._generating_id = None
        self._pending = None
        self._inspector.set_generating(False)
        if cid is not None and self._transcript.is_streaming():
            self._transcript.keep_stream()
            if self._cid == cid:
                self._error_plain_id = cid
        self._buffer = ""
        self._sync_enabled()
        if cid is not None:
            self.turn_finished.emit(cid)

    def on_unload_failed(self, code: str, message: str) -> None:
        if self._generating_id is not None:
            self._inspector.show_restart()
        if code != "generating":
            self.show_banner(message or code)

    def _on_send(self, text: str) -> None:
        cid = self._cid
        if cid is None or self._generating_id is not None or self._pending is not None:
            return
        if not text.strip():
            return
        self._inspector.flush_prompt()
        self._pending = ("send", cid, text)
        self._generating_id = cid
        self._buffer = ""
        self._rejected_drafts.pop(cid, None)
        self._rejected_banners.pop(cid, None)
        self._composer.clear()
        self._banner.hide()
        self._error_plain_id = None
        self._transcript.append_user(text)
        self._transcript.begin_stream()
        self._inspector.set_generating(True)
        self._sync_enabled()
        self.send_requested.emit(cid, text, self._inspector.params())

    def _on_system_prompt(self, conversation_id: int, text: str) -> None:
        self.system_prompt_changed.emit(conversation_id, text)

    def _on_inspector_toggled(self, checked: bool) -> None:
        self.set_inspector_open(checked)
        self.inspector_open_changed.emit(checked)

    def _on_inspector_unload(self) -> None:
        self.stop()
        self.unload_requested.emit()

    def _on_inspector_restart(self) -> None:
        self.stop()
        self.show_banner("Unload requested.")
        self.unload_requested.emit()
        self.restart_requested.emit()

    def _sync_enabled(self) -> None:
        has = self._cid is not None
        busy = self._generating_id is not None or self._pending is not None
        self._composer.setEnabled(has and not busy)
        self._regen.setEnabled(has and bool(self._transcript.turns()) and not busy)
        # System prompt stays editable during generate (next turn).
        self._inspector.setEnabled(has)
