"""QMainWindow three-column shell with streaming chat in column 3."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QSplitter, QWidget

from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import ModelRef
from llm_engine.services.chat import ChatService
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_manager_app.tokens import apply_studio
from llm_manager_app.widgets.chat_view import ChatView
from llm_manager_app.widgets.conversation_list import ConversationList, ConversationStore
from llm_manager_app.widgets.sidebar import CHATS, MODELS, Sidebar
from llm_manager_app.workers import (
    CatalogWorker,
    ChatWorker,
    start_catalog_worker,
    start_chat_worker,
)

_TITLE = "LLM Manager"


def _default_library() -> tuple[ConversationStore, object]:
    # Opened only when the caller did not inject a LibraryService (production).
    from llm_engine.config import resolve_db_path
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

        self._chat_service: ChatService | None = None
        self._worker: ChatWorker | None = None
        self._worker_thread: QThread | None = None
        self._catalog: CatalogWorker
        self._catalog_thread, self._catalog = start_catalog_worker(self._registry, self)
        if isinstance(library, LibraryService):
            self._chat_service = ChatService(library, self._session)
            self._worker_thread, self._worker = start_chat_worker(
                self._chat_service, self
            )

        shell = QWidget(self)
        shell.setObjectName("shell")
        splitter = QSplitter(Qt.Orientation.Horizontal, shell)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self._sidebar = Sidebar(splitter)
        self._sidebar.section_changed.connect(self._on_section)

        self._list = ConversationList(splitter, library=self._library)
        self._list.selected_id_changed.connect(self._on_selected)
        self._list.chat_created.connect(self._on_chat_created)

        self._chat_view = ChatView(splitter)
        self._chat_view.stop_requested.connect(self._on_stop)
        self._chat_view.turn_finished.connect(self._on_turn_finished)
        self._chat_view.model_selected.connect(self._on_model_selected)
        self._chat_view.manage_models_requested.connect(
            lambda: self._sidebar.select_section(MODELS)
        )
        self._chat_view.catalog_requested.connect(
            self._catalog.list_models, Qt.ConnectionType.QueuedConnection
        )
        self._catalog.listed.connect(self._chat_view.set_catalog)
        self._catalog.failed.connect(self._chat_view.on_catalog_failed)
        if self._worker is not None:
            # Queued: worker lives on a QThread after start_chat_worker.
            self._chat_view.send_requested.connect(self._worker.send)
            self._chat_view.regenerate_requested.connect(self._worker.regenerate)
            self._worker.accepted.connect(self._chat_view.on_accepted)
            self._worker.accepted.connect(self._on_chat_accepted)
            self._worker.rejected.connect(self._chat_view.on_rejected)
            self._worker.token.connect(self._chat_view.on_token)
            self._worker.done.connect(self._chat_view.on_done)
            self._worker.error.connect(self._chat_view.on_error)

        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._list)
        splitter.addWidget(self._chat_view)
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
        self._shortcut_find = QShortcut(QKeySequence.StandardKey.Find, self)
        self._shortcut_find.activated.connect(self._list.focus_search)
        self._shortcut_composer = QShortcut(QKeySequence("Ctrl+L"), self)
        self._shortcut_composer.activated.connect(self._chat_view.focus_composer)
        self._shortcut_stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._shortcut_stop.activated.connect(self._chat_view.stop)

        self._on_selected(self._list.selected_id())

    def closeEvent(self, event: QCloseEvent) -> None:
        service = self._chat_service
        if service is not None:
            with service._state_lock:
                active = service._active_id
            if active is not None:
                service.stop(active)
            worker = service._worker_thread
            if worker is not None:
                worker.join(2.0)
        qt_app = QApplication.instance()
        if qt_app is not None:
            qt_app.processEvents()
        if self._worker is not None:
            for sig in (
                self._worker.accepted,
                self._worker.rejected,
                self._worker.token,
                self._worker.done,
                self._worker.error,
            ):
                try:
                    sig.disconnect()
                except RuntimeError:
                    pass
        thread = self._worker_thread
        if thread is not None:
            thread.quit()
            thread.wait(2000)
            self._worker_thread = None
        catalog_thread = self._catalog_thread
        catalog_done = True
        if catalog_thread is not None:
            try:
                self._chat_view.catalog_requested.disconnect(self._catalog.list_models)
            except RuntimeError:
                pass
            try:
                self._catalog.listed.disconnect()
                self._catalog.failed.disconnect()
            except RuntimeError:
                pass
            catalog_thread.quit()
            # Ollama is_available/list_models can block for the 5s client timeout.
            catalog_done = catalog_thread.wait(6000)
            if catalog_done:
                self._catalog_thread = None
            else:
                # Keep Python refs; dropping them here lets GC kill a running QThread.
                app = QApplication.instance()
                catalog_thread.setParent(app)
                catalog_thread.finished.connect(self._on_catalog_finished)
                if catalog_thread.isFinished():
                    self._on_catalog_finished()
        if self._owns_registry and catalog_done:
            self._registry.close()
            self._owns_registry = False
        store = self._store
        closer = getattr(store, "close", None)
        if callable(closer):
            closer()
            self._store = None
        super().closeEvent(event)

    def _on_catalog_finished(self) -> None:
        thread = self._catalog_thread
        if thread is None:
            return
        if self._owns_registry:
            self._registry.close()
            self._owns_registry = False
        self._catalog_thread = None
        thread.deleteLater()

    def _on_section(self, _key: str) -> None:
        self._sync_title()

    def _on_chat_created(self, _cid: int) -> None:
        self._sidebar.select_section(CHATS)

    def _on_selected(self, cid: object) -> None:
        self._sync_title()
        if not isinstance(cid, int):
            self._chat_view.set_conversation(None)
            return
        if self._chat_view.is_streaming(cid) and self._chat_view.conversation_id() == cid:
            return
        if (
            self._chat_view.keeping_error_buffer(cid)
            and self._chat_view.conversation_id() == cid
        ):
            return
        getter = getattr(self._library, "get_conversation", None)
        if not callable(getter):
            self._chat_view.set_conversation(None)
            return
        self._chat_view.set_conversation(getter(cid))

    def _on_stop(self, conversation_id: int) -> None:
        # Set the cancel Event on the GUI thread; do not wait.
        if self._chat_service is not None:
            self._chat_service.stop(conversation_id)

    def _on_model_selected(self, ref: object) -> None:
        if not isinstance(ref, ModelRef):
            return
        cid = self._chat_view.conversation_id()
        if cid is None or self._chat_service is None:
            return
        self._chat_service.set_model(cid, ref)
        getter = getattr(self._library, "get_conversation", None)
        if callable(getter):
            self._chat_view.set_conversation(getter(cid))

    def _on_chat_accepted(self, cid: int, _kind: str) -> None:
        self._list.refresh(select_id=cid)
        self._sync_title()

    def _on_turn_finished(self, cid: int) -> None:
        self._list.refresh(select_id=cid)
        self._sync_title()

    def _sync_title(self) -> None:
        if self._sidebar.current_section() == MODELS:
            self.setWindowTitle(f"Models — {_TITLE}")
            return
        title = self._list.selected_title()
        self.setWindowTitle(title if title else _TITLE)
