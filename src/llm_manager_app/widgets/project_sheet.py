"""Embedded project form, shared by creation and the home guidance card."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llm_manager_app.model_names import ModelNames
from llm_manager_app.widgets.model_choice import ModelChoice


class ProjectSheet(QWidget):
    accepted = Signal()
    rejected = Signal()
    browse_requested = Signal()

    def __init__(self, parent=None, *, names=None, draft=("", "", None), editing=False):
        super().__init__(parent)
        self.setObjectName("projectSheet")
        self._names = names or ModelNames(parent=self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        title = QLabel("Edit project" if editing else "Create a project", self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self._name = QLineEdit(draft[0], self)
        self._name.setObjectName("projectName")
        self._name.setMaxLength(120)
        self._name.setPlaceholderText("e.g. My writing")
        layout.addWidget(QLabel("Project name", self))
        layout.addWidget(self._name)
        layout.addWidget(QLabel("Default model", self))
        self.choice = ModelChoice(self, names=self._names, empty="Use app default")
        self.choice.combo.setObjectName("projectModel")
        self.choice.set_current(draft[2])
        self._model = self.choice.combo
        layout.addWidget(self.choice)
        browse = QPushButton("Get more models…", self)
        browse.clicked.connect(self.browse_requested)
        layout.addWidget(browse, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(QLabel("Project guidance", self))
        self._instructions = QPlainTextEdit(draft[1], self)
        self._instructions.setObjectName("projectInstructions")
        self._instructions.setPlaceholderText("What should your model keep in mind?")
        self._instructions.setFixedHeight(104)
        self._instructions.setTabChangesFocus(True)
        layout.addWidget(self._instructions)
        hint = QLabel(
            "Used to start new chats. Existing chats keep their model and instructions.", self,
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.error = QLabel(self)
        self.error.setObjectName("settingsError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        actions = QHBoxLayout()
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        actions.addStretch()
        self._ok = QPushButton("Save" if editing else "Create project", self)
        self._ok.setObjectName("primaryButton")
        self._ok.clicked.connect(self.accept)
        self._name.textChanged.connect(lambda: self._ok.setEnabled(bool(self._name.text().strip())))
        self._ok.setEnabled(bool(self._name.text().strip()))
        actions.addWidget(self._ok)
        layout.addLayout(actions)
        layout.addStretch()

    def set_catalog(self, models, availability):
        self.choice.set_catalog(models, availability)

    def values(self):
        return self._name.text().strip(), self._instructions.toPlainText(), self.choice.current()

    def show_catalog_error(self, message):
        self.error.setText(message)
        self.error.show()

    def accept(self):
        if self._name.text().strip():
            self.accepted.emit()

    def reject(self):
        self.rejected.emit()
