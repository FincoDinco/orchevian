"""QMainWindow three-column shell. Chrome only: no inference, no composer."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListView,
    QMainWindow,
    QSplitter,
    QWidget,
)

from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.services.session import ModelSession
from llm_manager_app.tokens import apply_studio
from llm_manager_app.widgets.sidebar import CHATS, MODELS, Sidebar

_TITLE = "LLM Manager"


def _status_text(registry: BackendRegistry, session: ModelSession) -> str:
    status = session.status()
    loaded = status.loaded.ref.id if status.loaded is not None else "none"
    lines = [
        f"loaded: {loaded}",
        f"generating: {'yes' if status.generating else 'no'}",
    ]
    try:
        _models, availability = registry.list_models()
    except EngineError as exc:
        lines.append(f"registry: {exc}")
        return "\n".join(lines)
    for name, (ok, reason) in availability.items():
        if ok:
            lines.append(f"{name}: available")
        else:
            lines.append(f"{name}: unavailable — {reason or 'unavailable'}")
    return "\n".join(lines)


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        registry: BackendRegistry | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_TITLE)
        self.setMinimumSize(1024, 680)
        self.resize(1280, 800)

        qt_app = QApplication.instance()
        if isinstance(qt_app, QApplication):
            apply_studio(qt_app)

        self._owns_registry = registry is None
        self._registry = registry if registry is not None else BackendRegistry()
        self._session = ModelSession(self._registry)

        shell = QWidget(self)
        shell.setObjectName("shell")
        splitter = QSplitter(Qt.Orientation.Horizontal, shell)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self._sidebar = Sidebar(splitter)
        self._sidebar.section_changed.connect(self._on_section)

        self._list = QListView(splitter)
        self._list.setObjectName("listPane")
        self._list.setSelectionMode(QListView.SelectionMode.SingleSelection)

        self._detail = QLabel(splitter)
        self._detail.setObjectName("detailPane")
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setContentsMargins(8, 8, 8, 8)
        self._detail.setText(_status_text(self._registry, self._session))

        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._list)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([200, 260, 820])

        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.setCentralWidget(shell)
        self._splitter = splitter

        self._shortcut_chats = QShortcut(QKeySequence("Ctrl+1"), self)
        self._shortcut_chats.activated.connect(lambda: self._sidebar.select_section(CHATS))
        self._shortcut_models = QShortcut(QKeySequence("Ctrl+2"), self)
        self._shortcut_models.activated.connect(lambda: self._sidebar.select_section(MODELS))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._owns_registry:
            self._registry.close()
        super().closeEvent(event)

    def _on_section(self, key: str) -> None:
        if key == MODELS:
            self.setWindowTitle(f"Models — {_TITLE}")
        else:
            self.setWindowTitle(_TITLE)
