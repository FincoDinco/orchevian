"""QMainWindow three-column shell. Conversation list in column 2; no composer."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QWidget,
)

from llm_engine.backends.registry import BackendRegistry
from llm_engine.services.session import ModelSession
from llm_manager_app.tokens import apply_studio
from llm_manager_app.widgets.conversation_list import ConversationList, ConversationStore
from llm_manager_app.widgets.sidebar import (
    CHATS,
    FOLDER_ALL,
    FOLDER_PROJECT,
    FOLDER_UNGROUPED,
    MODELS,
    Sidebar,
    SidebarSelection,
)

_TITLE = "LLM Manager"


def _status_text(session: ModelSession) -> str:
    # Session status only — list_models() is catalog I/O (Ollama HTTP / disk scan).
    status = session.status()
    loaded = status.loaded.ref.id if status.loaded is not None else "none"
    return (
        f"loaded: {loaded}\n"
        f"generating: {'yes' if status.generating else 'no'}"
    )


def _default_library() -> tuple[ConversationStore, object]:
    # Opened only when the caller did not inject a LibraryService (production).
    from llm_engine.config import resolve_db_path
    from llm_engine.store.library import LibraryService
    from llm_engine.store.sqlite import SqliteStore

    store = SqliteStore(resolve_db_path())
    return LibraryService(store), store


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        registry: BackendRegistry | None = None,
        library: ConversationStore | None = None,
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

        self._store: object | None = None
        if library is None:
            library, self._store = _default_library()
        self._library = library

        shell = QWidget(self)
        shell.setObjectName("shell")
        splitter = QSplitter(Qt.Orientation.Horizontal, shell)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self._sidebar = Sidebar(splitter, library=self._library)
        self._sidebar.section_changed.connect(self._on_section)
        self._sidebar.filter_changed.connect(self._on_filter)

        self._list = ConversationList(splitter, library=self._library)
        self._list.selected_id_changed.connect(lambda *_: self._sync_title())
        self._list.chat_created.connect(self._on_chat_created)

        self._detail = QLabel(splitter)
        self._detail.setObjectName("detailPane")
        self._detail.setWordWrap(True)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setContentsMargins(8, 8, 8, 8)
        self._detail.setText(_status_text(self._session))

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
        self._shortcut_new = QShortcut(QKeySequence.StandardKey.New, self)
        self._shortcut_new.activated.connect(self._list.new_chat)
        self._shortcut_new_project = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        self._shortcut_new_project.activated.connect(self._sidebar.new_project)
        self._shortcut_find = QShortcut(QKeySequence.StandardKey.Find, self)
        self._shortcut_find.activated.connect(self._list.focus_search)

        self._sync_title()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._owns_registry:
            self._registry.close()
        store = self._store
        closer = getattr(store, "close", None)
        if callable(closer):
            closer()
            self._store = None
        super().closeEvent(event)

    def _on_section(self, key: str) -> None:
        if key == MODELS:
            self._list.set_project_filter(...)
        self._sync_title()

    def _on_filter(self, selection: object) -> None:
        if not isinstance(selection, SidebarSelection):
            return
        if selection.folder == FOLDER_ALL:
            self._list.set_project_filter(...)
        elif selection.folder == FOLDER_UNGROUPED:
            self._list.set_project_filter(None)
        elif selection.folder == FOLDER_PROJECT:
            self._list.set_project_filter(
                selection.project_id, project_name=selection.project_name
            )
        self._sync_title()

    def _on_chat_created(self, _cid: int) -> None:
        self._sidebar.select_section(CHATS)
        self._sync_title()

    def _sync_title(self) -> None:
        if self._sidebar.current_section() == MODELS:
            self.setWindowTitle(f"Models — {_TITLE}")
            return
        title = self._list.selected_title()
        self.setWindowTitle(title if title else _TITLE)
