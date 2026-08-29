from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from llm_manager import database as db
from llm_manager.gui.widgets import PageHeader
from llm_manager.models import PromptTemplate


class TemplatesPage(QWidget):
    use_in_chat = Signal(str, str)  # system_prompt, user_prompt

    def __init__(self, parent=None):
        super().__init__(parent)
        self._templates: list[PromptTemplate] = []
        self._current_id: int | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        new_btn = QPushButton("+ New template")
        new_btn.setObjectName("primary")
        new_btn.clicked.connect(self._new_template)
        self._header = PageHeader("Prompt Templates", actions=[new_btn])
        layout.addWidget(self._header)

        splitter = QSplitter()
        splitter.setContentsMargins(28, 18, 28, 24)

        left = QWidget()
        left.setMaximumWidth(240)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        self._list = QListWidget()
        self._list.currentRowChanged.connect(self._on_select)
        left_layout.addWidget(self._list)
        splitter.addWidget(left)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_editor())

        empty = QLabel("No templates yet — create one to reuse prompts.")
        empty.setObjectName("emptyState")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(empty)
        splitter.addWidget(self._stack)

        splitter.setSizes([220, 640])
        layout.addWidget(splitter, 1)

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        right_layout = QVBoxLayout(editor)
        right_layout.setContentsMargins(16, 0, 0, 0)
        right_layout.setSpacing(10)

        right_layout.addWidget(self._label("Name"))
        self._name_edit = QLineEdit()
        right_layout.addWidget(self._name_edit)

        right_layout.addWidget(self._label("Description"))
        self._desc_edit = QLineEdit()
        right_layout.addWidget(self._desc_edit)

        right_layout.addWidget(self._label("System prompt"))
        self._system_edit = QTextEdit()
        self._system_edit.setPlaceholderText("You are a helpful assistant…")
        self._system_edit.setMaximumHeight(120)
        right_layout.addWidget(self._system_edit)

        right_layout.addWidget(self._label("User prompt template"))
        self._user_edit = QTextEdit()
        self._user_edit.setPlaceholderText("Summarize the following: {text}")
        right_layout.addWidget(self._user_edit)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save)
        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._delete)
        use_btn = QPushButton("Use in chat")
        use_btn.clicked.connect(self._use_in_chat)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(delete_btn)
        btn_row.addStretch()
        btn_row.addWidget(use_btn)
        right_layout.addLayout(btn_row)
        return editor

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("fieldLabel")
        return lbl

    def refresh(self):
        self._templates = db.list_templates()
        self._list.clear()
        for t in self._templates:
            self._list.addItem(QListWidgetItem(t.name))
        if self._templates:
            self._stack.setCurrentIndex(0)
            self._list.setCurrentRow(0)
        else:
            self._stack.setCurrentIndex(1)
            self._clear_editor()

    def _on_select(self, row: int):
        if row < 0 or row >= len(self._templates):
            return
        t = self._templates[row]
        self._current_id = t.id
        self._name_edit.setText(t.name)
        self._desc_edit.setText(t.description)
        self._system_edit.setPlainText(t.system_prompt)
        self._user_edit.setPlainText(t.user_prompt)

    def _clear_editor(self):
        self._current_id = None
        self._name_edit.clear()
        self._desc_edit.clear()
        self._system_edit.clear()
        self._user_edit.clear()

    def _new_template(self):
        self._stack.setCurrentIndex(0)
        self._list.clearSelection()
        self._clear_editor()
        self._name_edit.setFocus()

    def _save(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Enter a template name.")
            return
        self._current_id = db.save_template(
            name=name,
            description=self._desc_edit.text().strip(),
            system_prompt=self._system_edit.toPlainText(),
            user_prompt=self._user_edit.toPlainText(),
            template_id=self._current_id,
        )
        self.refresh()

    def _delete(self):
        if self._current_id is None:
            return
        if QMessageBox.question(self, "Delete template", "Delete this template?") == \
                QMessageBox.StandardButton.Yes:
            db.delete_template(self._current_id)
            self.refresh()

    def _use_in_chat(self):
        self.use_in_chat.emit(self._system_edit.toPlainText(), self._user_edit.toPlainText())
