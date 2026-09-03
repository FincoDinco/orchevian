"""New-project dialog: name plus optional instructions and default model."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QWidget,
)

from llm_engine.domain.models import BackendName, ModelRef


def parse_model_ref(text: str) -> ModelRef:
    stripped = text.strip()
    backend, sep, name = stripped.partition("/")
    if not sep or not backend.strip() or not name.strip():
        raise ValueError("expected backend/name")
    return ModelRef(BackendName(backend.strip().lower()), name.strip())


class ProjectSheet(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("projectSheet")
        self.setWindowTitle("New Project")
        self.setModal(True)

        self._name = QLineEdit(self)
        self._name.setObjectName("projectName")
        self._name.setPlaceholderText("Name")

        self._instructions = QPlainTextEdit(self)
        self._instructions.setObjectName("projectInstructions")
        self._instructions.setPlaceholderText("Optional instructions")
        self._instructions.setFixedHeight(96)

        self._model = QLineEdit(self)
        self._model.setObjectName("projectModel")
        self._model.setPlaceholderText("optional, e.g. ollama/qwen3:8b")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._ok is not None:
            self._ok.setEnabled(False)
        self._name.textChanged.connect(self._sync_ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.setContentsMargins(12, 12, 12, 12)
        form.setSpacing(8)
        form.addRow("Name", self._name)
        form.addRow("Instructions", self._instructions)
        form.addRow("Default model", self._model)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, ModelRef | None]:
        text = self._model.text().strip()
        model = parse_model_ref(text) if text else None
        return self._name.text().strip(), self._instructions.toPlainText(), model

    def accept(self) -> None:
        if not self._name.text().strip():
            return
        text = self._model.text().strip()
        if text:
            try:
                parse_model_ref(text)
            except ValueError:
                QMessageBox.warning(
                    self,
                    "Invalid model",
                    "Use backend/name, e.g. ollama/qwen3:8b",
                )
                return
        super().accept()

    def _sync_ok(self, _text: str) -> None:
        if self._ok is not None:
            self._ok.setEnabled(bool(self._name.text().strip()))
