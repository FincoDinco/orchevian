"""Project workspace: owned drafts, existing composer, chats, and editable guidance."""

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ModelRef
from llm_engine.store.library import resolve_new_chat_model
from llm_manager_app.model_names import BACKEND_TITLES
from llm_manager_app.widgets.attachments import AttachmentPanel
from llm_manager_app.widgets.composer import Composer
from llm_manager_app.widgets.model_picker import ModelPicker
from llm_manager_app.widgets.project_sheet import ProjectSheet


class ProjectHome(QScrollArea):
    start_requested = Signal(int, str, object)
    conversation_requested = Signal(int)
    browse_requested = Signal()
    project_saved = Signal(int)

    def __init__(self, parent=None, *, library, names, documents=None):
        super().__init__(parent)
        self.setObjectName("projectHome")
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._library = library
        self._names = names
        self.project = None
        self._drafts = {}
        self._creation_drafts = {}
        self._web_drafts = {}
        self._overrides = {}
        self._edit_drafts = {}
        self._models = []
        self._availability = {}
        self._ready = False
        self._busy = False
        self.editor = None
        body = QWidget(self)
        self.setWidget(body)
        self._layout = QVBoxLayout(body)
        self._layout.setContentsMargins(32, 24, 32, 24)
        self._layout.setSpacing(16)
        self.title = QLabel(body)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.title.setObjectName("pageTitle")
        self._layout.addWidget(self.title)
        self.picker = ModelPicker(body, names=names)
        self.picker.setObjectName("projectHomeModelPicker")
        self.picker.setMinimumWidth(280)
        self.picker.model_selected.connect(self._choose)
        self.picker.manage_models_requested.connect(self.browse_requested)
        self._layout.addWidget(self.picker, 0, Qt.AlignmentFlag.AlignLeft)
        self.composer = Composer(body)
        self.composer.setObjectName("projectComposer")
        self.composer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.composer._edit.setObjectName("projectComposerEdit")
        self.composer._send.setObjectName("projectSendButton")
        self.composer._edit.setPlaceholderText("Start a conversation in this project…")
        self.composer._edit.textChanged.connect(self._save_draft)
        self.composer.create_files.toggled.connect(self._save_draft)
        self.composer.web_search.toggled.connect(self._save_draft)
        self.composer.send_requested.connect(self._send)
        self._layout.addWidget(self.composer)
        self.status = QLabel(body)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("settingsHint")
        self._layout.addWidget(self.status)
        self.files = AttachmentPanel(body, project=True)
        self.files.service = documents
        self.files.disclosure.setChecked(True)
        self.files.busy_changed.connect(self._sync)
        self._layout.addWidget(self.files)
        self.files.setVisible(documents is not None)
        self.composer.files_dropped.connect(self.files.import_paths)
        heading = QLabel("Conversations", body)
        heading.setObjectName("sidebarSection")
        self._layout.addWidget(heading)
        self.chats = QListWidget(body)
        self.chats.setObjectName("projectConversations")
        self.chats.setSpacing(4)
        self.chats.itemActivated.connect(self._open_chat)
        self.chats.itemClicked.connect(self._open_chat)
        self._layout.addWidget(self.chats)
        self.empty = QLabel("Your project conversations will appear here.", body)
        self.empty.setObjectName("settingsHint")
        self._layout.addWidget(self.empty)
        self.guidance_card = QWidget(body)
        self.guidance_card.setObjectName("projectGuidanceCard")
        self.guidance_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card = QVBoxLayout(self.guidance_card)
        card.setContentsMargins(16, 16, 16, 16)
        row = QHBoxLayout()
        row.addWidget(QLabel("Project guidance", body))
        row.addStretch()
        self.edit = QPushButton("Edit project…", body)
        self.edit.clicked.connect(self.edit_project)
        row.addWidget(self.edit)
        card.addLayout(row)
        self.guidance = QLabel(body)
        self.guidance.setTextFormat(Qt.TextFormat.PlainText)
        self.guidance.setWordWrap(True)
        card.addWidget(self.guidance)
        self.default_label = QLabel(body)
        self.default_label.setTextFormat(Qt.TextFormat.PlainText)
        self.default_label.setWordWrap(True)
        self.default_label.setObjectName("settingsHint")
        card.addWidget(self.default_label)
        self._layout.addWidget(self.guidance_card)
        self._layout.addStretch()
        names.changed.connect(self.refresh_labels)
        self.verticalScrollBar().rangeChanged.connect(self._reveal_editor)

    def set_project(self, project):
        if self.project is not None and self.project.id != project.id:
            self._remove_editor(keep=True)
        self.project = project
        self.files.set_conversation(project.id)
        self.files.setVisible(self.files.service is not None)
        self.title.setText(project.name)
        creating = self._creation_drafts.get(project.id, False)
        searching = self._web_drafts.get(project.id, False)
        self.composer.set_text(self._drafts.get(project.id, ""))
        self.composer.create_files.setChecked(creating)
        self.composer.web_search.setChecked(searching)
        self.refresh_chats()
        self.refresh_labels()
        if project.id in self._edit_drafts and self.editor is None:
            self.edit_project()

    def _save_draft(self):
        if self.project is not None:
            self._drafts[self.project.id] = self.composer.text()
            self._creation_drafts[self.project.id] = self.composer.create_files.isChecked()
            self._web_drafts[self.project.id] = self.composer.web_search.isChecked()

    def effective_model(self):
        if self.project is None:
            return None
        return resolve_new_chat_model(
            self._overrides.get(self.project.id), self.project.default_model,
            getattr(self._library, "default_model", None),
        )

    def refresh_labels(self, *_args):
        if self.project is None:
            return
        self.guidance.setText(self.project.instructions or "Add guidance for new chats here.")
        ref = self.project.default_model or getattr(self._library, "default_model", None)
        source = "Project default" if self.project.default_model else "App default"
        self.default_label.setText(
            f"{source}: {self._names.display(ref)} · {BACKEND_TITLES[str(ref.backend)]}"
            if ref else "No default model. Choose a model above to start a chat."
        )
        self.picker.set_current(self.effective_model())
        self._sync()

    def set_catalog(self, models, availability):
        self._models = list(models)
        self._availability = dict(availability)
        self._ready = True
        self.picker.set_catalog(self._models, self._availability)
        if self.editor:
            self.editor.set_catalog(models, availability)
        self._sync()

    def set_busy(self, busy):
        if self._busy != busy:
            self._busy = busy
            self._sync()

    def _sync(self):
        ref = self.effective_model()
        model = next((m for m in self._models if m.ref == ref), None)
        ok, reason = (
            self._availability.get(str(ref.backend), (True, None)) if ref else (False, None)
        )
        available = self._ready and model is not None and model.available and ok
        message = (
            "Another model operation is running. Your draft stays here." if self._busy
            else "Checking model availability…" if not self._ready
            else "Choose a model to start a conversation." if ref is None
            else reason or "Runtime unavailable. Restore it or choose another model." if not ok
            else "Model missing. Restore it or choose another model." if model is None
            else model.unavailable_reason or "Runtime unavailable. Choose another model."
            if not model.available else ""
        )
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.composer.set_importing(self.files.busy())
        self.composer.set_send_enabled(available and not self._busy)
        self.files.set_generating(self._busy)
        self.picker.setEnabled(self._ready and not self._busy)

    def _choose(self, ref):
        if self.project is not None and isinstance(ref, ModelRef):
            self._overrides[self.project.id] = ref
            self.refresh_labels()

    def _send(self, text):
        if self.project is not None and not self.files.busy() and not self._busy:
            self.start_requested.emit(self.project.id, text, self.effective_model())

    def sent(self):
        self.composer.clear()

    def refresh_chats(self):
        if self.project is None:
            return
        self.chats.clear()
        for chat in self._library.list_conversations(project_id=self.project.id):
            item = QListWidgetItem(chat.title)
            item.setSizeHint(QSize(0, 42))
            item.setData(Qt.ItemDataRole.UserRole, chat.id)
            item.setToolTip(chat.title)
            self.chats.addItem(item)
        self.chats.setFixedHeight(min(280, max(50, self.chats.count() * 46 + 8)))
        self.empty.setVisible(self.chats.count() == 0)
        self.chats.setVisible(self.chats.count() > 0)

    def _open_chat(self, item):
        self.conversation_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def edit_project(self):
        if self.project is None or self.editor is not None:
            return
        p = self.project
        self.editor = ProjectSheet(
            self.widget(), names=self._names, editing=True,
            draft=self._edit_drafts.get(p.id, (p.name, p.instructions, p.default_model)),
        )
        self.editor.set_catalog(self._models, self._availability)
        self.editor.accepted.connect(self._save_project)
        self.editor.rejected.connect(lambda: self._remove_editor(keep=False))
        self.editor.browse_requested.connect(self.browse_requested)
        self._layout.insertWidget(self._layout.count() - 1, self.editor)
        self.guidance_card.hide()
        QTimer.singleShot(0, self._reveal_editor)

    def _reveal_editor(self, *_args):
        if self.editor is not None:
            self.ensureWidgetVisible(self.editor._ok, 0, 24)

    def _remove_editor(self, *, keep):
        if self.editor is not None:
            if keep:
                self._edit_drafts[self.project.id] = self.editor.values()
            else:
                self._edit_drafts.pop(self.project.id, None)
            self._layout.removeWidget(self.editor)
            self.editor.deleteLater()
            self.editor = None
        self.guidance_card.show()

    def _save_project(self):
        name, guidance, model = self.editor.values()
        try:
            self.project = self._library.update_project(
                self.project.id, name=name, instructions=guidance, model=model,
            )
        except Exception as exc:
            self.editor.show_catalog_error(str(exc))
            return
        self._remove_editor(keep=False)
        self.title.setText(name)
        self.refresh_labels()
        self.project_saved.emit(self.project.id)
