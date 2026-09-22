"""Compact, read-only storage details within the model library."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from llm_engine.services.storage import StorageSummary
from llm_manager_app.icons import icon
from llm_manager_app.model_names import BACKEND_TITLES


def size_text(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return ""


class StoragePanel(QWidget):
    def __init__(self, parent=None, *, reveal) -> None:
        super().__init__(parent)
        self.setObjectName("storageSummary")
        self._summary = None
        self._reveal = reveal
        self._toggle = QPushButton("Storage", self)
        self._toggle.setObjectName("storageToggle")
        self._toggle.setCheckable(True)
        self._toggle.setIcon(icon("chevron-right"))
        self._toggle.toggled.connect(self._toggle_details)
        self._details = QWidget(self)
        self._body = QLabel("Refresh the library to see model sizes and free space.", self)
        self._body.setObjectName("storageBody")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._reveal_btn = QPushButton("Reveal model folder", self)
        self._reveal_btn.setObjectName("storageReveal")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._open_folder)
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 4, 0, 0)
        details.addWidget(self._body)
        details.addWidget(self._reveal_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._details.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._details)

    def _toggle_details(self, expanded: bool) -> None:
        self._details.setVisible(expanded)
        self._toggle.setIcon(icon("chevron-down" if expanded else "chevron-right"))

    def apply_summary(self, value) -> None:
        if not isinstance(value, StorageSummary):
            self._summary = None
            self._body.setText(f"Storage details unavailable: {value}")
            self._reveal_btn.setEnabled(False)
            return
        self._summary = value
        self._reveal_btn.setEnabled(True)
        rows = []
        for backend in value.backends:
            name = BACKEND_TITLES.get(backend.backend, backend.backend)
            if not backend.available and not backend.model_count:
                rows.append(f"{name}: unavailable — size unknown")
            else:
                row = f"{name}: {size_text(backend.size_bytes)} · {backend.model_count} models"
                if not backend.available:
                    row += " (runtime unavailable)"
                rows.append(row)
        rows.append(
            f"Free on model drive: {size_text(value.free_bytes)}"
            if value.free_bytes is not None
            else "Free on model drive: unavailable"
        )
        rows.append(f"Model folder: {value.model_dir}")
        rows.append(
            "Catalog sizes may share files; totals are not unique disk usage. "
            "Ollama manages its own model folder."
        )
        self._body.setText("\n".join(rows))
        self._body.setToolTip(value.disk_error or "")

    def _open_folder(self) -> None:
        if self._summary is not None:
            self._reveal(self._summary.model_dir)
