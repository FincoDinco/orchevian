"""Chat workspace: transcript, composer, inspector, picker, stop, regenerate."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ChatTurn, Conversation, GenerationParams, LocalModel, ModelRef
from llm_manager_app.icons import icon
from llm_manager_app.model_names import ModelNames
from llm_manager_app.widgets.composer import Composer
from llm_manager_app.widgets.inspector import Inspector
from llm_manager_app.widgets.labels import ElidedLabel
from llm_manager_app.widgets.model_picker import ModelPicker
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
    model_selected = Signal(object)
    manage_models_requested = Signal()
    catalog_requested = Signal()
    new_chat_requested = Signal()
    remember_requested = Signal()
    end_private_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, names: ModelNames | None = None) -> None:
        super().__init__(parent)
        self._names = names or ModelNames(parent=self)
        self._names.changed.connect(self._refresh_model_name)
        self.setObjectName("detailPane")
        self._cid: int | None = None
        self._model: ModelRef | None = None
        self._generating_id: int | None = None
        self._buffer = ""
        self._pending: tuple[str, int, str] | None = None
        self._error_plain_id: int | None = None
        self._undo_assistant: ChatTurn | None = None
        self._rejected_drafts: dict[int, str] = {}
        self._rejected_banners: dict[int, str] = {}
        self._catalog_error: str | None = None
        self._catalog_ready = False
        self._session_busy = False
        self._session_busy_copy = "Model is loading or generating."

        self._empty = self._welcome()

        self._banner = QLabel(self)
        self._banner.setObjectName("chatBanner")
        self._banner.setWordWrap(True)
        self._banner.hide()

        self._picker = ModelPicker(self, names=self._names)
        self._picker.setMaximumWidth(160)
        self._picker.setEnabled(False)
        self._picker.model_selected.connect(self._on_pick)
        self._picker.manage_models_requested.connect(self.manage_models_requested)
        self._picker.catalog_requested.connect(self.catalog_requested)

        self._inspector_btn = QPushButton(self)
        self._inspector_btn.setToolTip("Chat settings")
        self._inspector_btn.setAccessibleName("Chat settings")
        self._inspector_btn.setIcon(icon("settings"))
        self._inspector_btn.setObjectName("inspectorToggle")
        self._inspector_btn.setCheckable(True)
        self._inspector_btn.setChecked(True)
        self._inspector_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._inspector_btn.toggled.connect(self._on_inspector_toggled)

        self._regen = QPushButton(self)
        self._regen.setToolTip("Regenerate response")
        self._regen.setAccessibleName("Regenerate response")
        self._regen.setIcon(icon("refresh"))
        self._regen.setObjectName("regenerateButton")
        self._regen.setCursor(Qt.CursorShape.PointingHandCursor)
        self._regen.setEnabled(False)
        self._regen.clicked.connect(self.regenerate)

        toolbar = QWidget(self)
        toolbar.setObjectName("chatToolbar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(24, 8, 24, 8)
        toolbar_layout.setSpacing(8)
        self._title = ElidedLabel("New conversation", toolbar)
        self._title.setObjectName("chatTitle")
        toolbar_layout.addWidget(self._title, 1)
        toolbar_layout.addWidget(self._inspector_btn)
        toolbar_layout.addWidget(self._regen)
        self._remember = QToolButton(self)
        self._remember.setObjectName("rememberChatButton")
        self._remember.setIcon(icon("brain"))
        self._remember.setToolTip("Remember this conversation in your Second Brain")
        self._remember.setAccessibleName("Remember this conversation")
        self._remember.setAutoRaise(True)
        self._remember.setEnabled(False)
        self._remember.clicked.connect(self.remember_requested)
        toolbar_layout.addWidget(self._remember)

        self._transcript = Transcript(self)
        self._model_empty = QLabel(self)
        self._model_empty.setObjectName("modelEmpty")
        self._model_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._model_empty.setWordWrap(True)

        self._open_models = QPushButton("Open Models", self)
        self._open_models.setObjectName("openModelsButton")
        self._open_models.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open_models.clicked.connect(self.manage_models_requested)

        empty_pane = QWidget(self)
        empty_pane.setObjectName("modelEmptyPane")
        empty_layout = QVBoxLayout(empty_pane)
        empty_layout.setContentsMargins(16, 16, 16, 16)
        empty_layout.setSpacing(16)
        empty_layout.addStretch(1)
        model_title = QLabel("Choose a model to begin", empty_pane)
        model_title.setObjectName("welcomeTitle")
        model_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        model_title.setWordWrap(True)
        empty_layout.addWidget(model_title)
        empty_layout.addWidget(self._model_empty)
        empty_layout.addWidget(self._open_models, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)

        self._content = QStackedWidget(self)
        self._content.addWidget(self._transcript)
        self._content.addWidget(empty_pane)
        self._empty_pane = empty_pane

        self._starters = self._welcome(starters=True)
        self._content.addWidget(self._starters)

        self._composer = Composer(self)
        self._composer.send_requested.connect(self._on_send)
        self._composer.stop_requested.connect(self.stop)
        self._composer.setEnabled(False)
        self._composer.set_send_enabled(False)

        body = QWidget(self)
        body.setObjectName("chatBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(toolbar)
        reading = QWidget(body)
        reading.setMaximumWidth(820)
        reading_layout = QVBoxLayout(reading)
        reading_layout.setContentsMargins(28, 12, 28, 16)
        reading_layout.setSpacing(16)
        reading_layout.addWidget(self._banner)
        reading_layout.addWidget(self._content, 1)
        # Keep model selection next to the message, as in the reference composer.
        self._composer.layout().insertWidget(1, self._picker, 0, Qt.AlignmentFlag.AlignBottom)
        reading_layout.addWidget(self._composer)
        reading_row = QHBoxLayout()
        reading_row.setContentsMargins(0, 0, 0, 0)
        reading_row.addStretch(1)
        reading_row.addWidget(reading, 100)
        reading_row.addStretch(1)
        body_layout.addLayout(reading_row, 1)

        self._inspector = Inspector(self)
        self._inspector.system_prompt_changed.connect(self._on_system_prompt)
        self._inspector.unload_requested.connect(self._on_inspector_unload)
        self._inspector.restart_requested.connect(self._on_inspector_restart)
        self._inspector.collapse_requested.connect(self._toggle_inspector)

        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setObjectName("chatSplitter")
        split.setChildrenCollapsible(False)
        split.setHandleWidth(1)
        split.addWidget(body)
        split.addWidget(self._inspector)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([580, 320])
        self._chat_split = split

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(split)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._private_banner = QWidget(self)
        private_outer = QVBoxLayout(self._private_banner)
        private_outer.setContentsMargins(16, 8, 16, 8)
        private_panel = QWidget(self._private_banner)
        private_panel.setObjectName("privateChatBanner")
        private_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        private_outer.addWidget(private_panel)
        private_layout = QHBoxLayout(private_panel)
        private_layout.setContentsMargins(20, 12, 20, 12)
        self._private_notice = QLabel(
            "<b>Private chat</b> · No saved history · No memories<br>"
            "Only this private chat provides context. Saved chats and memories are never used. "
            "Clear private chat to erase it and return to your workspace.", private_panel,
        )
        self._private_notice.setWordWrap(True)
        private_layout.addWidget(self._private_notice, 1)
        end = QPushButton("Clear private chat", self._private_banner)
        end.setObjectName("clearPrivateChatButton")
        end.setIcon(icon("lock"))
        end.clicked.connect(self.end_private_requested)
        private_layout.addWidget(end)
        self._private_banner.hide()
        layout.addWidget(self._private_banner)
        layout.addWidget(self._stack)

    def _welcome(self, *, starters: bool = False) -> QWidget:
        pane = QWidget(self)
        pane.setObjectName("welcomePane")
        layout = QVBoxLayout(pane)
        if starters:
            layout.setContentsMargins(24, 12, 24, 12)
        else:
            layout.setContentsMargins(36, 32, 36, 32)
        layout.setSpacing(12 if starters else 16)
        layout.addStretch(2)
        title = QLabel(
            "What would you like to work on?" if starters else "Your models. Your workspace.", pane
        )
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)
        subtitle = QLabel(
            "A fresh conversation, with a model you choose."
            if starters
            else "Select a conversation, or start something new.\n"
            "A quiet place to think, write, and build with local AI.",
            pane,
        )
        subtitle.setObjectName("chatEmpty" if not starters else "welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addSpacing(4 if starters else 12)
        if starters:
            for label, prompt in (
                ("Explore an idea", "Help me think through an idea: "),
                ("Write a first draft", "Help me write a first draft of "),
                ("Work through a problem", "Help me work through this problem: "),
            ):
                button = QPushButton(label, pane)
                button.setObjectName("starterButton")
                button.setIcon(icon("arrow"))
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, text=prompt: self._use_starter(text))
                layout.addWidget(button)
        else:
            actions = QHBoxLayout()
            actions.addStretch()
            new = QPushButton("New conversation", pane)
            new.setObjectName("welcomeNewChat")
            new.clicked.connect(self.new_chat_requested)
            models = QPushButton("Browse models", pane)
            models.clicked.connect(self.manage_models_requested)
            for button in (new, models):
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                actions.addWidget(button)
            actions.addStretch()
            layout.addLayout(actions)
        layout.addStretch(3)
        if starters:
            # Keep wrapped headings and starter actions intact in compact layouts.
            layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            scroll = QScrollArea(self)
            scroll.setObjectName("welcomeScroll")
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(pane)
            return scroll
        return pane

    def _use_starter(self, text: str) -> None:
        self._composer.set_text(text)
        self._composer.focus_edit()

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

    def picker(self) -> ModelPicker:
        return self._picker

    def inspector_open(self) -> bool:
        return self._inspector.is_expanded()

    def set_inspector_open(self, visible: bool) -> None:
        blocked = self._inspector_btn.blockSignals(True)
        self._inspector_btn.setChecked(visible)
        self._inspector_btn.blockSignals(blocked)
        self._inspector.set_expanded(visible)
        self._inspector.setVisible(visible)
        rail = self._inspector.tab_width()
        sizes = self._chat_split.sizes()
        total = sum(sizes) or 860
        if visible:
            width = max(280, rail)
            if len(sizes) != 2 or sizes[1] < width:
                self._chat_split.setSizes([max(1, total - width), width])
        else:
            self._chat_split.setSizes([total, 0])

    def _toggle_inspector(self) -> None:
        self.set_inspector_open(not self.inspector_open())
        self.inspector_open_changed.emit(self.inspector_open())

    def _on_inspector_toggled(self, checked: bool) -> None:
        self.set_inspector_open(checked)
        self.inspector_open_changed.emit(checked)

    def set_return_sends(self, enabled: bool) -> None:
        self._composer.set_return_sends(enabled)

    def refresh_theme(self) -> None:
        self._transcript.refresh_theme()
        self.update()

    def _refresh_model_name(self, *_args: object) -> None:
        self._transcript.set_assistant_label(
            self._names.display(self._model) if self._model else "Assistant"
        )

    def generation_params(self) -> GenerationParams:
        return self._inspector.params()

    def banner_text(self) -> str:
        return self._banner.text() if self._banner.isVisible() else ""

    def show_banner(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.show()

    def set_session_busy(self, busy: bool) -> None:
        self._session_busy = busy
        if busy:
            self.show_banner(self._session_busy_copy)
        elif self._banner.text() == self._session_busy_copy:
            self._banner.hide()
            self._banner.clear()
        self._sync_enabled()

    def _sync_session_busy_banner(self) -> None:
        if self._session_busy:
            self.show_banner(self._session_busy_copy)

    def set_catalog(self, models: object, availability: object) -> None:
        model_list: list[LocalModel] = []
        if isinstance(models, list):
            model_list = [item for item in models if isinstance(item, LocalModel)]
        avail = dict(availability) if isinstance(availability, dict) else {}
        self._catalog_error = None
        self._catalog_ready = True
        self._picker.set_catalog(model_list, avail)
        self._sync_enabled()

    def on_catalog_failed(self, code: str, message: str) -> None:
        self._catalog_error = message or code
        self._catalog_ready = True
        self._sync_enabled()

    def clear_private(self, cid: int) -> None:
        """Drop UI copies too, including drafts, streaming text, and inspector guidance."""
        self._inspector.flush_prompt()
        self._rejected_drafts.pop(cid, None)
        self._rejected_banners.pop(cid, None)
        if self._generating_id == cid:
            self._generating_id = None
        self._pending = None
        self._buffer = ""
        self._undo_assistant = None
        self._inspector.set_generating(False)
        self.set_conversation(None)

    def set_conversation(self, conversation: Conversation | None) -> None:
        private = conversation is not None and conversation.summary.id < 0
        self._private_banner.setVisible(private)
        self._remember.setVisible(not private)
        self._inspector_btn.setVisible(not private)
        if private:
            self.set_inspector_open(False)
        self._title.setText(conversation.summary.title if conversation else "New conversation")
        if conversation is None:
            self._cid = None
            self._model = None
            self._pending = None
            self._error_plain_id = None
            self._undo_assistant = None
            self._transcript.clear()
            self._banner.hide()
            self._composer.clear()
            self._composer.setEnabled(False)
            self._composer.set_send_enabled(False)
            self._regen.setEnabled(False)
            self._remember.setEnabled(False)
            self._picker.set_current(None)
            self._picker.setEnabled(False)
            self._inspector.set_conversation(None)
            self._content.setCurrentWidget(self._transcript)
            self._stack.setCurrentWidget(self._empty)
            return
        outgoing = self._cid
        if outgoing is not None and outgoing != conversation.summary.id:
            self._inspector.flush_prompt()
        self._model = conversation.summary.model
        self._picker.set_current(self._model)
        self._transcript.set_assistant_label(
            self._names.display(self._model) if self._model is not None else "Assistant"
        )
        self.catalog_requested.emit()
        cid = conversation.summary.id
        if self.is_streaming(cid):
            self._cid = cid
            self._stack.setCurrentWidget(self._chat_split)
            self._transcript.set_turns(conversation.messages)
            self._transcript.restore_stream(self._buffer)
            self._inspector.set_conversation(conversation)
            self._inspector.set_generating(True)
            self._sync_enabled()
            self._sync_session_busy_banner()
            return
        if self.keeping_error_buffer(cid) and self._cid == cid:
            self._stack.setCurrentWidget(self._chat_split)
            self._sync_enabled()
            self._sync_session_busy_banner()
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
        self._sync_session_busy_banner()
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
        if cid is None or self._model is None:
            return
        if self._generating_id is not None or self._pending is not None or self._session_busy:
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
        if self._pending is not None and self._pending[1] == conversation_id:
            self._pending = None
        self._undo_assistant = None
        self._sync_enabled()

    def on_rejected(self, conversation_id: int, code: str, message: str) -> None:
        if conversation_id < 0 and conversation_id != self._cid:
            return
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
        if cancelled and self._cid == conversation_id:
            self.show_banner(
                "Stopped. Clear private chat when you are finished."
                if conversation_id < 0 else "Stopped. Your conversation has been kept."
            )

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

    def _on_pick(self, ref: object) -> None:
        if not isinstance(ref, ModelRef):
            return
        self._model = ref
        self._picker.set_current(ref)
        self._transcript.set_assistant_label(self._names.display(ref))
        self._sync_enabled()
        self.model_selected.emit(ref)

    def _on_send(self, text: str) -> None:
        cid = self._cid
        if cid is None or self._model is None:
            return
        if self._generating_id is not None or self._pending is not None or self._session_busy:
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
        has_model = has and self._model is not None
        generating = self._generating_id is not None or self._pending is not None
        busy = generating or self._session_busy
        self._picker.setEnabled(has and self._catalog_ready and not busy)
        self._composer.setEnabled(has)
        self._composer.set_generating(generating)
        self._composer.set_blocked(self._session_busy and not generating)
        self._composer.set_send_enabled(has_model and not busy)
        self._regen.setEnabled(has_model and bool(self._transcript.turns()) and not busy)
        self._remember.setEnabled(
            has_model and self._cid >= 0 and bool(self._transcript.turns()) and not busy
        )
        self._inspector.setEnabled(has)
        self._sync_empty()

    def _sync_empty(self) -> None:
        if self._cid is None:
            return
        if (
            self._model is not None
            or self._transcript.turns()
            or self.is_streaming()
            or self.keeping_error_buffer(self._cid)
        ):
            show_starters = (
                self._model is not None
                and not self._transcript.turns()
                and not self.is_streaming()
                and not self.keeping_error_buffer(self._cid)
            )
            self._content.setCurrentWidget(self._starters if show_starters else self._transcript)
            return
        reason = self._catalog_error or self._picker.unavailable_copy()
        if self._catalog_ready and not self._picker.has_models():
            text = "No models available."
            if reason:
                text = f"{text}\n{reason}"
            self._open_models.show()
        else:
            text = "Select a model to start chatting."
            self._open_models.hide()
        self._model_empty.setText(text)
        self._content.setCurrentWidget(self._empty_pane)
