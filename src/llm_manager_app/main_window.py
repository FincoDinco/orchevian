"""Desktop workspace: unified sidebar and a dedicated chat or model surface."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from llm_engine.backends.registry import BackendRegistry
from llm_engine.config import default_log_path, resolve_db_path
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import ModelRef
from llm_engine.services.catalog import CatalogService
from llm_engine.services.chat import ChatService
from llm_engine.services.memory import CaptureResult
from llm_engine.services.openai_api import ApiServerService
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.vault import MemoryVault
from llm_manager_app.icons import icon
from llm_manager_app.model_names import ModelNames
from llm_manager_app.model_preferences import default_model
from llm_manager_app.tokens import apply_studio
from llm_manager_app.widgets.chat_view import ChatView
from llm_manager_app.widgets.conversation_list import ConversationList, ConversationStore
from llm_manager_app.widgets.downloads_view import DownloadsPopover
from llm_manager_app.widgets.memory_view import MemoryView
from llm_manager_app.widgets.models_view import ModelsView
from llm_manager_app.widgets.project_home import ProjectHome
from llm_manager_app.widgets.project_sheet import ProjectSheet
from llm_manager_app.widgets.settings import (
    APP_NAME,
    KEY_AUTO_MEMORY,
    KEY_INSPECTOR_OPEN,
    KEY_LAST_CONVERSATION_ID,
    KEY_RETURN_SENDS,
    ORG_NAME,
    SettingsDialog,
    ShortcutsDialog,
    as_bool,
    as_int,
    ensure_appearance,
    make_settings,
)
from llm_manager_app.widgets.sidebar import (
    CHATS,
    FOLDER_ALL,
    FOLDER_PROJECT,
    MEMORY,
    MODELS,
    PRIVATE,
    SETTINGS,
    TEMPLATES,
    Sidebar,
    SidebarSelection,
)
from llm_manager_app.widgets.templates_view import TemplatesView
from llm_manager_app.widgets.web_search_settings import load_search_keys
from llm_manager_app.workers import (
    CatalogWorker,
    ChatWorker,
    start_catalog_worker,
    start_chat_worker,
)

_TITLE = APP_NAME


def _default_library() -> tuple[ConversationStore, object]:
    # Opened only when the caller did not inject a LibraryService (production).
    from llm_engine.config import resolve_db_path
    from llm_engine.store.sqlite import SqliteStore

    store = SqliteStore(resolve_db_path())
    return LibraryService(store), store


class MainWindow(QMainWindow):
    memory_requested = Signal(int, object, object)
    model_load_requested = Signal(object, object)
    chat_send_requested = Signal(int, str, object, object, object, bool)
    chat_regenerate_requested = Signal(int, object, object, object, bool)

    def __init__(
        self,
        *,
        registry: BackendRegistry | None = None,
        library: ConversationStore | None = None,
        settings: QSettings | None = None,
        memory_vault: MemoryVault | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_TITLE)
        # Keep the native titlebar and resize frame available on every platform.
        self.setMinimumSize(1024, 680)
        self.resize(1280, 800)

        self._settings = settings if settings is not None else make_settings()
        self._model_names = ModelNames(self._settings, self)
        qt_app = QApplication.instance()
        if isinstance(qt_app, QApplication):
            if not qt_app.organizationName():
                qt_app.setOrganizationName(ORG_NAME)
            if not qt_app.applicationName():
                qt_app.setApplicationName(APP_NAME)
            apply_studio(qt_app, theme=ensure_appearance(self._settings))

        self._owns_registry = registry is None
        self._registry = registry if registry is not None else BackendRegistry()
        self._session = ModelSession(self._registry)
        self._catalog_service = CatalogService(self._registry, self._session)

        self._store: object | None = None
        if library is None:
            library, self._store = _default_library()
        self._library = library
        self._library.default_model = default_model(self._settings)
        self._catalog_models = []
        self._catalog_availability = {}
        self._catalog_ready = False
        self._project_form = None
        self._project_return = None
        self._closing = False
        self._private_id: int | None = None
        self._private_return_draft = ""
        self._private_return_inspector = False
        self._pending_memories: set[int] = set()
        self._automatic_capture = False
        self._memory_busy = False
        self._memory_cancel = threading.Event()
        self._model_load_cancel = threading.Event()
        self._chat_cancel = threading.Event()
        self._stop_requested = False
        db_path = library._store.path if isinstance(library, LibraryService) else resolve_db_path()
        vault_path = self._settings.value("memory/vault", str(db_path.parent / "second-brain"))
        self._memory_vault = memory_vault or MemoryVault(Path(str(vault_path)))

        self._chat_service: ChatService | None = None
        self._api: ApiServerService | None = None
        self._api_busy = False
        self._worker: ChatWorker | None = None
        self._worker_thread: QThread | None = None
        self._catalog: CatalogWorker
        self._catalog_thread, self._catalog = start_catalog_worker(self._registry, self)
        self._linger_threads: list[QThread] = []
        if isinstance(library, LibraryService):
            self._chat_service = ChatService(
                library,
                self._session,
                memory_vault=self._memory_vault
                if as_bool(self._settings.value("memory/recall", True), True)
                else None,
            )
            # Optional search-service keys from Settings → Web Search.
            self._chat_service.web.keys = load_search_keys(self._settings)
            self._worker_thread, self._worker = start_chat_worker(
                self._chat_service, self, session=self._session
            )
            self._api = ApiServerService(self._chat_service, self._catalog_service)

        shell = QWidget(self)
        shell.setObjectName("shell")
        splitter = QSplitter(Qt.Orientation.Horizontal, shell)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)

        self._sidebar = Sidebar(splitter, library=self._library, names=self._model_names)
        self._sidebar.section_changed.connect(self._on_section)
        self._sidebar.filter_changed.connect(self._on_filter)
        self._sidebar.collapsed_changed.connect(self._on_sidebar_collapsed)

        self._list = ConversationList(
            self._sidebar, library=self._library, names=self._model_names,
        )
        self._list.set_embedded()
        self._sidebar.attach_conversations(self._list)
        self._sidebar.new_chat_requested.connect(lambda: self._list.new_chat())
        self._sidebar.settings_requested.connect(self._open_settings)
        self._sidebar.search_requested.connect(self._focus_search)
        self._sidebar.catalog_requested.connect(
            self._catalog.list_models, Qt.ConnectionType.QueuedConnection
        )
        self._list.selected_id_changed.connect(self._on_selected)
        self._list.conversation_activated.connect(self._show_selected_chat)
        self._list.chat_created.connect(self._on_chat_created)
        self._models = ModelsView(
            catalog=self._catalog_service, external_jobs=True, names=self._model_names,
        )
        self._sidebar.project_setup_requested.connect(self._create_project)
        self._models.return_to_project.clicked.connect(self._return_to_project)
        self._models.chat_requested.connect(self._on_chat_with_model)
        self._models.refresh_requested.connect(
            self._catalog.list_models, Qt.ConnectionType.QueuedConnection
        )

        self._workspace = QWidget(splitter)
        self._workspace_layout = QVBoxLayout(self._workspace)
        self._workspace_layout.setContentsMargins(0, 0, 0, 0)
        self._workspace_layout.setSpacing(0)
        self._detail_stack = QStackedWidget(self._workspace)
        self._workspace_layout.addWidget(self._detail_stack, 1)
        self._detail_stack.setObjectName("detailPane")
        self._chat_view = ChatView(self._detail_stack, names=self._model_names)
        if self._chat_service is not None:
            self._chat_view.attachments.service = self._chat_service.documents
            self._chat_view.artifacts.service = self._chat_service.artifacts
            self._chat_view.web_sources.service = self._chat_service.web
            self._chat_view.composer()._attach.show()
        self._chat_view.new_chat_requested.connect(lambda: self._list.new_chat())
        self._chat_view.stop_requested.connect(self._on_stop)
        self._chat_view.turn_finished.connect(self._on_turn_finished)
        self._chat_view.end_private_requested.connect(self._clear_private)
        self._chat_view.system_prompt_changed.connect(self._on_system_prompt)
        self._chat_view.inspector_open_changed.connect(self._on_inspector_open)
        self._chat_view.model_selected.connect(self._on_model_selected)
        self._chat_view.manage_models_requested.connect(self._manage_chat_models)
        self._chat_view.catalog_requested.connect(
            self._catalog.list_models, Qt.ConnectionType.QueuedConnection
        )
        self._catalog.listed.connect(self._on_catalog_listed)
        self._catalog.storage_updated.connect(self._models.storage.apply_summary)
        self._catalog.failed.connect(self._on_catalog_failed)
        self._detail_stack.addWidget(self._chat_view)
        self._templates = None
        if isinstance(self._library, LibraryService):
            self._templates = TemplatesView(self._detail_stack, library=self._library)
            self._templates.use_requested.connect(self._use_template)
            self._detail_stack.addWidget(self._templates)
        self._project_home = ProjectHome(
            self._detail_stack, library=self._library, names=self._model_names,
            documents=self._chat_service.documents if self._chat_service is not None else None,
        )
        self._project_home.files.files_changed.connect(self._chat_view.attachments.refresh)
        self._chat_view.artifacts.project_changed.connect(self._project_home.files.refresh)
        self._chat_view.artifacts.project_changed.connect(self._chat_view.attachments.refresh)
        self._detail_stack.addWidget(self._project_home)
        self._project_home.start_requested.connect(self._start_project_chat)
        self._project_home.conversation_requested.connect(self._open_project_chat)
        self._project_home.browse_requested.connect(self._browse_project_models)
        self._project_home.project_saved.connect(
            lambda pid: self._sidebar.refresh(select_project_id=pid)
        )
        self._models.embed_detail()
        self._detail_stack.addWidget(self._models)
        self._downloads_popover = DownloadsPopover(self._models.download_view, self)
        self._models.downloads_requested.connect(self._show_downloads)
        self._models.download_activity_changed.connect(self._sync_download_button)
        self._models.download_view.browse_requested.connect(self._browse_models)
        self._models.download_view.open_model.connect(self._open_downloaded_model)
        self._memory = MemoryView(self._memory_vault, self._detail_stack)
        self._detail_stack.addWidget(self._memory)
        self._memory.remember_requested.connect(self._remember_conversation)
        self._chat_view.remember_requested.connect(self._remember_conversation)
        self._memory.cancel_requested.connect(self._cancel_memory)
        self._memory.vault_changed.connect(self._on_vault_changed)
        self._memory.recall_changed.connect(self._on_memory_recall)
        self._memory.source_requested.connect(self._open_memory_source)
        self._memory._recall.setChecked(as_bool(self._settings.value("memory/recall", True), True))
        if self._worker is not None:
            self.memory_requested.connect(self._worker.remember, Qt.ConnectionType.QueuedConnection)
            self._worker.memories_created.connect(self._on_memories_created)
            self._worker.memories_failed.connect(self._on_memories_failed)
            # Queued: worker lives on a QThread after start_chat_worker.
            self._chat_view.send_requested.connect(self._on_chat_busy_started)
            self._chat_view.send_requested.connect(self._queue_send)
            self.chat_send_requested.connect(self._worker.send, Qt.ConnectionType.QueuedConnection)
            self._chat_view.regenerate_requested.connect(self._on_chat_busy_started)
            self._chat_view.regenerate_requested.connect(self._queue_regenerate)
            self.chat_regenerate_requested.connect(
                self._worker.regenerate, Qt.ConnectionType.QueuedConnection
            )
            self._chat_view.unload_requested.connect(self._worker.unload)
            self._models.load_requested.connect(self._on_models_load)
            self._models.stop_requested.connect(self._force_stop_model)
            self.model_load_requested.connect(
                self._worker.catalog_load, Qt.ConnectionType.QueuedConnection
            )
            self._models.unload_requested.connect(self._on_models_unload)
            self._models.unload_requested.connect(
                self._worker.catalog_unload, Qt.ConnectionType.QueuedConnection
            )
            self._models.delete_requested.connect(self._on_models_unload)
            self._models.delete_requested.connect(
                self._worker.catalog_delete, Qt.ConnectionType.QueuedConnection
            )
            self._worker.accepted.connect(self._chat_view.on_accepted)
            self._worker.accepted.connect(self._on_chat_accepted)
            self._worker.accepted.connect(self._on_chat_busy_started)
            self._worker.rejected.connect(self._chat_view.on_rejected)
            self._worker.rejected.connect(self._on_chat_busy_ended)
            self._worker.token.connect(self._chat_view.on_token)
            self._worker.artifact_progress.connect(self._on_artifact_progress)
            self._worker.web_progress.connect(self._chat_view.on_web_progress)
            self._worker.done.connect(self._chat_view.on_done)
            self._worker.done.connect(self._on_chat_busy_ended)
            self._worker.done.connect(self._schedule_automatic_memory)
            self._worker.error.connect(self._chat_view.on_error)
            self._worker.error.connect(self._on_chat_busy_ended)
            self._worker.unloaded.connect(self._chat_view.on_unloaded)
            self._worker.unloaded.connect(self._on_chat_busy_ended)
            self._worker.unload_failed.connect(self._chat_view.on_unload_failed)
            self._worker.catalog_loaded.connect(self._on_catalog_model_loaded)
            self._worker.catalog_unloaded.connect(self._on_catalog_model_unloaded)
            self._worker.catalog_deleted.connect(self._on_catalog_model_deleted)
            self._worker.catalog_failed.connect(self._on_catalog_model_failed)

        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._workspace)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1000])

        layout = QHBoxLayout(shell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.setCentralWidget(shell)
        self._splitter = splitter

        self.statusBar().hide()
        self._model_status_timer = QTimer(self)
        self._model_status_timer.setInterval(100)
        self._model_status_timer.timeout.connect(self._sync_model_activity)
        self._model_status_timer.start()

        self._auto_memory_timer = QTimer(self)
        self._auto_memory_timer.setInterval(1500)
        self._auto_memory_timer.timeout.connect(self._drain_automatic_memories)
        self._auto_memory_timer.start()
        self._settings_dialog: SettingsDialog | None = None
        self._shortcuts_dialog: ShortcutsDialog | None = None
        self._build_menus()
        self._build_toolbar()

        # Escape is a QShortcut so dialogs can still consume it; other keys are QActions.
        self._shortcut_stop = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._shortcut_stop.activated.connect(self._stop_current)

        self._restore_chrome()
        self._project_home.composer.set_return_sends(self._chat_view.composer().return_sends())
        last = as_int(self._settings.value(KEY_LAST_CONVERSATION_ID))
        self._list.refresh(select_id=last)
        self._on_selected(self._list.selected_id())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._templates is not None and not self._templates.prepare_close():
            event.ignore()
            return
        if not self._memory.prepare_close():
            event.ignore()
            return
        if not self._chat_view.attachments.shutdown():
            self._chat_view.show_banner("Finishing document cancellation. Close again in a moment.")
            event.ignore()
            return
        if not self._project_home.files.shutdown():
            self._project_home.status.setText(
                "Finishing file cancellation. Close again in a moment."
            )
            self._project_home.status.show()
            event.ignore()
            return
        if self._settings_dialog is not None:
            self._settings_dialog.wait_for_api_change()
        if self._api is not None:
            try:
                self._api.stop()
            except EngineError as exc:
                QMessageBox.warning(self, "API is still stopping", str(exc))
                event.ignore()
                return
        self._closing = True
        self._auto_memory_timer.stop()
        self._pending_memories.clear()
        self._leave_private()
        self._downloads_popover.hide()
        self._model_status_timer.stop()
        self._model_load_cancel.set()
        self._chat_cancel.set()
        self._memory_cancel.set()
        if self._chat_service is not None:
            self._chat_service.cancel_current()
        self._persist_chrome()
        try:
            self._session.force_unload()
        except Exception:
            pass
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
        models_done = self._models.shutdown()
        if self._worker is not None:
            for sig in (
                self._worker.accepted,
                self._worker.rejected,
                self._worker.token,
                self._worker.done,
                self._worker.error,
                self._worker.unloaded,
                self._worker.unload_failed,
                self._worker.catalog_loaded,
                self._worker.catalog_unloaded,
                self._worker.catalog_deleted,
                self._worker.catalog_failed,
                self._worker.memories_created,
                self._worker.memories_failed,
                self._worker.artifact_progress,
                self._worker.web_progress,
            ):
                try:
                    sig.disconnect()
                except RuntimeError:
                    pass
        thread = self._worker_thread
        chat_done = True
        if thread is not None:
            thread.quit()
            chat_done = thread.wait(2000)
            self._worker_thread = None
            if not chat_done:
                self._linger(thread)
        catalog_thread = self._catalog_thread
        catalog_done = True
        if catalog_thread is not None:
            try:
                self._chat_view.catalog_requested.disconnect(self._catalog.list_models)
            except RuntimeError:
                pass
            try:
                self._models.refresh_requested.disconnect(self._catalog.list_models)
            except RuntimeError:
                pass
            try:
                self._catalog.listed.disconnect()
                self._catalog.storage_updated.disconnect()
                self._catalog.failed.disconnect()
            except RuntimeError:
                pass
            catalog_thread.quit()
            catalog_done = catalog_thread.wait(6000)
            self._catalog_thread = None
            if not catalog_done:
                self._linger(catalog_thread)
        if not models_done:
            lingering = self._models.take_running_thread(QApplication.instance())
            if lingering is not None:
                self._linger(lingering)
            for thread in self._models.downloads.take_running_threads(QApplication.instance()):
                self._linger(thread)
        if (
            self._owns_registry
            and chat_done
            and catalog_done
            and models_done
            and not self._linger_threads
        ):
            self._registry.close()
            self._owns_registry = False
        store = self._store
        closer = getattr(store, "close", None)
        if callable(closer):
            closer()
            self._store = None
        super().closeEvent(event)

    def _linger(self, thread: QThread) -> None:
        app = QApplication.instance()
        thread.setParent(app)
        thread.finished.connect(self._on_linger_finished)
        self._linger_threads.append(thread)
        if thread.isFinished():
            self._on_linger_finished()

    def _on_linger_finished(self) -> None:
        self._linger_threads = [item for item in self._linger_threads if item.isRunning()]
        if self._linger_threads:
            return
        if self._owns_registry:
            self._registry.close()
            self._owns_registry = False

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Workspace", self._workspace)
        toolbar.setObjectName("workspaceToolbar")
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._workspace_layout.insertWidget(0, toolbar)
        self._workspace_toolbar = toolbar
        from llm_manager_app.widgets.model_activity import ModelActivity

        self._model_activity = ModelActivity(toolbar)
        self._model_activity.stop_requested.connect(self._force_stop_model)
        self._force_stop = self._model_activity.stop
        # The action stays visible; ModelActivity delays and fades its own visibility.
        self._model_activity_action = toolbar.addWidget(self._model_activity)
        self._model_activity.bind_action(self._model_activity_action)
        spacer = QWidget(toolbar)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self._private_button = QToolButton(toolbar)
        self._private_button.setObjectName("privateChatButton")
        self._private_button.setText("Private Chat")
        self._private_button.setIcon(icon("lock"))
        self._private_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._private_button.setToolTip("Open Private Chat — no saved history or memories")
        self._private_button.setAccessibleName("Open Private Chat")
        self._private_button.clicked.connect(lambda: self._sidebar.select_section(PRIVATE))
        toolbar.addWidget(self._private_button)
        self._download_button = QToolButton(toolbar)
        self._download_button.setObjectName("downloadsButton")
        self._download_button.setIcon(icon("download"))
        self._download_button.setIconSize(QSize(20, 20))
        self._download_button.setAutoRaise(True)
        self._download_button.setCheckable(True)
        self._download_button.clicked.connect(self._toggle_downloads)
        toolbar.addWidget(self._download_button)
        self._downloads_popover.dismissed.connect(lambda: self._download_button.setChecked(False))
        self._sync_download_button(self._models.downloads.active_count)

    def _sync_download_button(self, count: int) -> None:
        if not hasattr(self, "_download_button"):
            return
        self._download_button.setText(str(count) if count else "Downloads")
        self._download_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon if count
            else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        label = f"Downloads, {count} active" if count else "Downloads"
        self._download_button.setToolTip(label)
        self._download_button.setAccessibleName(label)

    def _toggle_downloads(self) -> None:
        if self._downloads_popover.isVisible():
            self._downloads_popover.hide()
        else:
            self._show_downloads()

    def _show_downloads(self) -> None:
        if self._closing or self._private_id is not None:
            return
        self._downloads_popover.show_for(self._download_button)
        self._download_button.setChecked(True)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        view_menu = self.menuBar().addMenu("&View")
        chat_menu = self.menuBar().addMenu("&Chat")
        help_menu = self.menuBar().addMenu("&Help")

        new_chat = QAction("New Chat", self)
        new_chat.setObjectName("newChatAction")
        new_chat.setShortcut(QKeySequence.StandardKey.New)
        new_chat.triggered.connect(lambda: self._list.new_chat())
        file_menu.addAction(new_chat)
        self._new_chat_action = new_chat
        self._shortcut_new = new_chat

        new_project = QAction("New Project", self)
        new_project.setObjectName("newProjectAction")
        new_project.setShortcut(QKeySequence("Ctrl+Shift+N"))
        new_project.triggered.connect(lambda: self._sidebar.new_project())
        file_menu.addAction(new_project)
        self._shortcut_new_project = new_project

        file_menu.addSeparator()

        private_act = QAction("Private chat", self)
        private_act.setShortcut(QKeySequence("Ctrl+Shift+P"))
        private_act.triggered.connect(lambda: self._sidebar.select_section(PRIVATE))
        file_menu.addAction(private_act)

        settings_act = QAction("Settings…", self)
        settings_act.setObjectName("settingsAction")
        settings_act.setShortcut(QKeySequence.StandardKey.Preferences)
        settings_act.setMenuRole(QAction.MenuRole.PreferencesRole)
        settings_act.triggered.connect(self._open_settings)
        file_menu.addAction(settings_act)
        self._settings_action = settings_act

        sidebar_act = QAction("Hide Sidebar", self)
        sidebar_act.setObjectName("toggleSidebarAction")
        sidebar_act.setShortcut(QKeySequence("Ctrl+Meta+S" if sys.platform == "darwin"
                                              else "Ctrl+Shift+S"))
        sidebar_act.triggered.connect(self._sidebar.toggle_collapsed)
        self._sidebar.collapsed_changed.connect(
            lambda collapsed: sidebar_act.setText("Show Sidebar" if collapsed else "Hide Sidebar")
        )
        sidebar_act.setText("Show Sidebar" if self._sidebar.is_collapsed() else "Hide Sidebar")
        view_menu.addAction(sidebar_act)
        view_menu.addSeparator()

        chats_act = QAction("Chats", self)
        chats_act.setObjectName("chatsAction")
        chats_act.setShortcut(QKeySequence("Ctrl+1"))
        chats_act.triggered.connect(lambda: self._sidebar.select_section(CHATS))
        view_menu.addAction(chats_act)
        self._shortcut_chats = chats_act

        models_act = QAction("Models", self)
        models_act.setObjectName("modelsAction")
        models_act.setShortcut(QKeySequence("Ctrl+2"))
        models_act.triggered.connect(lambda: self._sidebar.select_section(MODELS))
        view_menu.addAction(models_act)
        self._shortcut_models = models_act

        memory_act = QAction("Second Brain", self)
        memory_act.setObjectName("memoryAction")
        memory_act.setShortcut(QKeySequence("Ctrl+3"))
        memory_act.triggered.connect(lambda: self._sidebar.select_section(MEMORY))
        view_menu.addAction(memory_act)
        if self._templates is not None:
            templates_act = QAction("Templates", self)
            templates_act.setShortcut(QKeySequence("Ctrl+5"))
            templates_act.triggered.connect(lambda: self._sidebar.select_section(TEMPLATES))
            view_menu.addAction(templates_act)
        downloads_act = QAction("Downloads", self)
        downloads_act.setShortcut(QKeySequence("Ctrl+4"))
        downloads_act.triggered.connect(self._show_downloads)
        view_menu.addAction(downloads_act)

        find_act = QAction("Find", self)
        find_act.setObjectName("findAction")
        find_act.setShortcut(QKeySequence.StandardKey.Find)
        find_act.triggered.connect(self._focus_search)
        view_menu.addAction(find_act)
        self._shortcut_find = find_act

        delete_act = QAction("Delete Conversation", self)
        delete_act.setObjectName("deleteConversationAction")
        delete_act.triggered.connect(self._delete_conversation)
        view_menu.addAction(delete_act)

        composer_act = QAction("Focus Composer", self)
        composer_act.setObjectName("focusComposerAction")
        composer_act.setShortcut(QKeySequence("Ctrl+L"))
        composer_act.triggered.connect(self._focus_composer)
        chat_menu.addAction(composer_act)
        self._shortcut_composer = composer_act

        force_stop = QAction("Force Stop Model", self)
        force_stop.setObjectName("forceStopModelAction")
        force_stop.setShortcut(QKeySequence("Ctrl+Shift+."))
        force_stop.triggered.connect(self._force_stop_model)
        chat_menu.addAction(force_stop)
        self._force_stop_action = force_stop
        force_stop.setEnabled(False)

        shortcuts_act = QAction("Keyboard Shortcuts", self)
        shortcuts_act.setObjectName("shortcutsAction")
        shortcuts_act.triggered.connect(self._open_shortcuts)
        help_menu.addAction(shortcuts_act)

    def _restore_chrome(self) -> None:
        self._chat_view.set_return_sends(
            as_bool(self._settings.value(KEY_RETURN_SENDS, True), True)
        )
        self._chat_view.set_inspector_open(
            as_bool(self._settings.value(KEY_INSPECTOR_OPEN, False), False)
        )

    def _persist_chrome(self) -> None:
        self._settings.setValue(KEY_INSPECTOR_OPEN, self._chat_view.inspector_open())
        self._settings.setValue(KEY_RETURN_SENDS, self._chat_view.composer().return_sends())
        cid = self._list.selected_id()
        if cid is not None:
            self._settings.setValue(KEY_LAST_CONVERSATION_ID, cid)
        else:
            self._settings.remove(KEY_LAST_CONVERSATION_ID)
        self._settings.sync()

    def _default_model_changed(self, ref):
        self._library.default_model = ref
        self._project_home.refresh_labels()

    def _create_project(self):
        if self._private_id is not None:
            return
        self._sidebar.select_section(CHATS)
        if self._project_form is None:
            self._project_form = ProjectSheet(self._detail_stack, names=self._model_names)
            self._project_form.accepted.connect(self._save_new_project)
            self._project_form.rejected.connect(self._cancel_new_project)
            self._project_form.browse_requested.connect(self._browse_project_models)
            self._detail_stack.addWidget(self._project_form)
        if self._catalog_ready:
            self._project_form.set_catalog(self._catalog_models, self._catalog_availability)
        self._detail_stack.setCurrentWidget(self._project_form)
        self._chat_view.catalog_requested.emit()
        self._sync_title()
        self._project_form._name.setFocus()

    def _discard_project_form(self):
        if self._project_return is self._project_form:
            self._project_return = None
        self._models.return_to_project.hide()
        self._detail_stack.removeWidget(self._project_form)
        self._project_form.deleteLater()
        self._project_form = None

    def _save_new_project(self):
        name, guidance, model = self._project_form.values()
        try:
            project = self._library.create_project(name, guidance, model)
        except Exception as exc:
            self._project_form.show_catalog_error(str(exc))
            return
        self._discard_project_form()
        self._sidebar.refresh(select_project_id=project.id)

    def _cancel_new_project(self):
        self._discard_project_form()
        self._on_filter(self._sidebar.current_selection())

    def _return_to_project(self):
        if self._private_id is not None:
            return
        target = self._project_return
        self._project_return = None
        self._models.return_to_project.hide()
        if target is self._project_home and self._project_home.project is not None:
            self._sidebar.select_project(self._project_home.project.id)
        elif target is not None and target is self._project_form:
            self._create_project()
        self._sync_title()

    def _show_selected_chat(self):
        self._sidebar.select_section(CHATS)
        self._detail_stack.setCurrentWidget(self._chat_view)
        self._on_selected(self._list.selected_id())
        self._sync_title()

    def _open_project_chat(self, cid):
        self._list.select_id(cid)
        self._show_selected_chat()

    def _start_project_chat(self, pid, text, ref):
        self._sync_model_activity()
        if (self._project_home._busy or self._project_home.files.busy()
                or self._private_id is not None):
            return
        try:
            conversation = self._library.create_conversation(project_id=pid, model=ref)
        except Exception as exc:
            self._project_home.status.setText(str(exc))
            self._project_home.status.show()
            return
        self._list.refresh(select_id=conversation.summary.id)
        self._show_selected_chat()
        self._chat_view.composer().set_text(text)
        self._chat_view.composer().create_files.setChecked(
            self._project_home.composer.create_files.isChecked()
        )
        self._chat_view.composer().web_search.setChecked(
            self._project_home.composer.web_search.isChecked()
        )
        self._chat_view.composer().submit()
        # A rejected send retains its text in the regular chat's recovery path.
        self._project_home.sent()
        self._project_home.refresh_chats()

    def _open_settings(self) -> None:
        self._sidebar.select_section(SETTINGS)

    def _show_settings(self) -> None:
        if self._settings_dialog is None:
            db_path = getattr(self._store, "path", None)
            self._settings_dialog = SettingsDialog(
                self._detail_stack,
                settings=self._settings,
                names=self._model_names,
                db_path=Path(db_path) if db_path is not None else None,
                log_path=default_log_path(),
                api=self._api,
            )
            self._settings_dialog.default_model_changed.connect(self._default_model_changed)
            self._settings_dialog.return_sends_changed.connect(
                self._project_home.composer.set_return_sends
            )
            self._settings_dialog.appearance_changed.connect(self._on_appearance)
            self._settings_dialog.return_sends_changed.connect(self._chat_view.set_return_sends)
            self._settings_dialog.rescan_requested.connect(self._rescan_catalog)
            self._settings_dialog.automatic_memory_changed.connect(self._on_automatic_memory)
            self._settings_dialog.web_search_keys_changed.connect(self._on_search_keys)
            self._settings_dialog.back_requested.connect(
                lambda: self._sidebar.select_section(CHATS)
            )
            self._detail_stack.addWidget(self._settings_dialog)
        self._settings_dialog.reload()
        if self._catalog_ready:
            self._settings_dialog.set_catalog(self._catalog_models, self._catalog_availability)
        self._detail_stack.setCurrentWidget(self._settings_dialog)
        self._chat_view.catalog_requested.emit()

    def _open_shortcuts(self) -> None:
        if self._shortcuts_dialog is None:
            self._shortcuts_dialog = ShortcutsDialog(self)
        self._shortcuts_dialog.show()
        self._shortcuts_dialog.raise_()
        self._shortcuts_dialog.activateWindow()

    def _on_appearance(self, theme: str) -> None:
        qt_app = QApplication.instance()
        if isinstance(qt_app, QApplication):
            apply_studio(qt_app, theme=theme)
        self._chat_view.refresh_theme()
        self._sidebar.refresh_theme()
        self._memory.refresh_theme()
        for view in self.findChildren(QAbstractItemView):
            viewport = view.viewport()
            if viewport is not None:
                viewport.update()
        self.update()

    def _on_inspector_open(self, visible: bool) -> None:
        self._settings.setValue(KEY_INSPECTOR_OPEN, visible)

    def _on_sidebar_collapsed(self, _collapsed: bool) -> None:
        self._sync_sidebar_size()

    def _sync_sidebar_size(self) -> None:
        if self._private_id is not None:
            return
        sizes = self._splitter.sizes()
        if len(sizes) != 2:
            return
        rail = self._sidebar.tab_width()
        if self._sidebar.is_collapsed():
            extra = max(0, sizes[0] - rail)
            self._splitter.setSizes([rail, sizes[1] + extra])
            return
        target = max(280, self._sidebar.minimumWidth())
        take = max(0, target - sizes[0])
        self._splitter.setSizes([target, max(1, sizes[1] - take)])

    def _on_system_prompt(self, conversation_id: int, text: str) -> None:
        if self._chat_service is not None:
            self._chat_service.set_system_prompt(conversation_id, text)

    def _set_private_workspace(self, active: bool) -> None:
        if active:
            self._private_sidebar_sizes = self._splitter.sizes()
            self._downloads_popover.hide()
            self._sidebar.hide()
            self._workspace_toolbar.hide()
            allowed = {self._force_stop_action, self._shortcut_composer}
            self._private_disabled_actions = {
                action: action.isEnabled()
                for menu in self.menuBar().actions() if menu.menu() is not None
                for action in menu.menu().actions()
                if action not in allowed and not action.isSeparator()
            }
            for action in self._private_disabled_actions:
                action.setEnabled(False)
        else:
            self._sidebar.show()
            self._workspace_toolbar.show()
            self._splitter.setSizes(self._private_sidebar_sizes)
            for action, enabled in self._private_disabled_actions.items():
                action.setEnabled(enabled)
            self._private_disabled_actions.clear()

    def _clear_private(self) -> None:
        if self._private_id is None:
            return
        self._leave_private()
        self._sidebar.end_private()
        self._on_selected(self._list.selected_id())
        self._chat_view.composer().set_text(self._private_return_draft)
        self._private_return_draft = ""

    def _leave_private(self) -> None:
        cid = self._private_id
        if cid is None or self._chat_service is None:
            return
        self._chat_cancel.set()
        self._chat_view.clear_private(cid)
        self._chat_service.discard_private(cid)
        self._private_id = None
        self._set_private_workspace(False)
        self._chat_view.set_inspector_open(self._private_return_inspector)

    def _on_section(self, key: str) -> None:
        if key != PRIVATE and self._private_id is not None:
            return
        if key == PRIVATE:
            if self._chat_service is not None and self._private_id is None:
                self._chat_view.inspector().flush_prompt()
                self._private_return_draft = self._chat_view.composer().text()
                self._private_return_inspector = self._chat_view.inspector_open()
                conversation = self._chat_service.create_private()
                self._private_id = conversation.summary.id
                self._set_private_workspace(True)
                self._chat_view.composer().clear()
                self._chat_view.set_conversation(conversation)
            self._detail_stack.setCurrentWidget(self._chat_view)
            self._sync_memory_available()
        elif key == SETTINGS:
            self._show_settings()
        elif key == MODELS:
            self._list.set_project_filter(...)
            self._detail_stack.setCurrentWidget(self._models)
            self._models.refresh()
        elif key == MEMORY:
            self._detail_stack.setCurrentWidget(self._memory)
            self._memory.refresh()
            self._sync_memory_available()
        elif key == TEMPLATES and self._templates is not None:
            self._detail_stack.setCurrentWidget(self._templates)
            self._templates.refresh()
        else:
            self._detail_stack.setCurrentWidget(self._chat_view)
        self._sync_sidebar_size()
        self._sync_title()

    def _manage_chat_models(self) -> None:
        if self._private_id is not None:
            self._chat_view.show_banner("Clear private chat to browse or download more models.")
            return
        self._sidebar.select_section(MODELS)

    def _browse_models(self) -> None:
        self._downloads_popover.hide()
        self._sidebar.select_section(MODELS)
        self._models._tabs.setCurrentIndex(1)

    def _browse_project_models(self) -> None:
        self._project_return = self._detail_stack.currentWidget()
        self._models.return_to_project.show()
        self._browse_models()

    def _open_downloaded_model(self, ref: ModelRef) -> None:
        if self._private_id is not None:
            return
        self._downloads_popover.hide()
        self._sidebar.select_section(MODELS)
        self._models.open_downloaded_model(ref)

    def _on_chat_with_model(self, ref: object) -> None:
        if self._private_id is not None:
            return
        if isinstance(ref, ModelRef):
            self._list.new_chat(model=ref)
            return
        self._sidebar.select_section(CHATS)

    def _delete_conversation(self) -> None:
        if self._sidebar.current_section() != CHATS:
            return
        self._list.delete_selected()

    def _focus_search(self) -> None:
        if self._sidebar.current_section() == TEMPLATES and self._templates is not None:
            self._templates.focus_search()
            return
        if self._sidebar.current_section() == MEMORY:
            self._memory.focus_search()
            return
        if self._sidebar.current_section() != CHATS:
            self._models.focus_search()
            return
        self._sidebar.set_collapsed(False)
        self._list.focus_search()

    def _focus_composer(self) -> None:
        if self._sidebar.current_section() not in {CHATS, PRIVATE}:
            return
        if self._detail_stack.currentWidget() is self._project_home:
            self._project_home.composer.focus_edit()
        else:
            self._chat_view.focus_composer()

    def _use_template(self, template_id: int) -> None:
        if self._private_id is not None:
            return
        try:
            template = self._library.get_template(template_id)
            conversation = self._library.create_conversation(template_id=template_id)
        except Exception as exc:
            self._templates._status.setText(str(exc))
            return
        self._chat_view.inspector().flush_prompt()
        self._sidebar.select_all()
        self._list.refresh(select_id=conversation.summary.id)
        self._show_selected_chat()
        self._chat_view.composer().set_text(template.user_prompt)
        self._chat_view.focus_composer()

    def _rescan_catalog(self) -> None:
        if not self._models.refresh():
            self._chat_view.catalog_requested.emit()

    def _on_catalog_listed(self, models: object, availability: object) -> None:
        self._catalog_models = list(models)
        self._catalog_availability = dict(availability)
        self._catalog_ready = True
        self._project_home.set_catalog(models, availability)
        if self._project_form is not None:
            self._project_form.set_catalog(models, availability)
        if self._settings_dialog is not None:
            self._settings_dialog.set_catalog(models, availability)
        self._sidebar.set_catalog(models, availability)
        self._chat_view.set_catalog(models, availability)
        self._models.apply_listed(models, availability)
        if self._models.job_kind() == "refresh":
            self._models.finish_job()

    def _on_catalog_failed(self, code: str, message: str) -> None:
        if self._project_form is not None:
            self._project_form.show_catalog_error(message or code)
        self._project_home.status.setText(message or code)
        self._project_home.status.show()
        if self._settings_dialog is not None:
            self._settings_dialog._default_model.hint.setText(message or code)
            self._settings_dialog._default_model.hint.show()
        self._chat_view.on_catalog_failed(code, message)
        if self._models.job_kind() == "refresh":
            self._models.apply_failed(EngineError(code, message))
            self._models.finish_job()

    def _on_models_load(self, ref: object) -> None:
        self._stop_requested = False
        self._model_load_cancel = threading.Event()
        self._chat_view.set_session_busy(True)
        self.model_load_requested.emit(ref, self._model_load_cancel)
        self._sync_model_activity()

    def _queue_send(self, cid: int, text: str, params: object) -> None:
        self._stop_requested = False
        self._chat_cancel = threading.Event()
        self.chat_send_requested.emit(
            cid, text, params, self._chat_cancel, self._chat_view.artifacts.request(),
            self._chat_view.composer().web_search.isChecked(),
        )

    def _queue_regenerate(self, cid: int, params: object) -> None:
        self._stop_requested = False
        self._chat_cancel = threading.Event()
        self.chat_regenerate_requested.emit(
            cid, params, self._chat_cancel, self._chat_view.artifacts.request(),
            self._chat_view.composer().web_search.isChecked(),
        )

    def _on_artifact_progress(self, cid, message):
        if self._chat_view.conversation_id() == cid and self._chat_view.is_streaming(cid):
            self._chat_view.artifacts.notice.setText(message)
            self._chat_view.artifacts.show()

    def _sync_model_activity(self) -> None:
        if self._closing:
            return
        status = self._session.status()
        busy = (
            status.generating or self._memory_busy
            or self._models.job_kind() in {"load", "unload"}
            or (self._chat_service is not None and self._chat_service._generating)
            or self._chat_view._pending is not None
        )
        self._project_home.set_busy(busy)
        api_busy = bool(self._api and self._api.status()["active_requests"])
        if api_busy or self._api_busy:
            self._chat_view.set_session_busy(
                api_busy or self._memory_busy or self._models.job_kind() in {"load", "unload"}
            )
            self._models.set_chat_busy(busy)
            self._models.sync_from_session()
            self._sync_memory_available()
        self._api_busy = api_busy
        private = self._private_id is not None
        self._workspace_toolbar.setVisible(not private or busy)
        self._private_button.setVisible(not private)
        self._download_button.setVisible(not private)
        self._force_stop_action.setEnabled(busy and not self._stop_requested)
        self._model_activity_action.setEnabled(True)
        if not busy:
            self._stop_requested = False
        message = (
            "Stopping model…"
            if self._stop_requested
            else f"Loading {self._model_names.display(status.loading)}…"
            if status.loading is not None
            else "Loading model…" if self._models.job_kind() == "load"
            else "Unloading model…" if self._models.job_kind() == "unload"
            else "Remembering this chat…" if self._memory_busy
            else "Responding to API…" if api_busy
            else "Model is thinking…" if self._chat_view._transcript._reasoning_active
            else "Reading document context…" if not self._chat_view._buffer and (
                self._chat_view.attachments is not None
                and self._chat_view.attachments.has_documents()
            )
            else "Waiting for model…" if not self._chat_view._buffer
            else "Generating response…"
        )
        self._model_activity.set_activity(busy, message, self._stop_requested)

    def _on_search_keys(self, keys: list) -> None:
        if self._chat_service is not None:
            self._chat_service.web.keys = list(keys)

    def _force_stop_model(self) -> None:
        if self._closing or self._stop_requested:
            return
        self._stop_requested = True
        # Do not queue this behind catalog_load/remember on the occupied QThread.
        self._model_load_cancel.set()
        self._chat_cancel.set()
        self._memory_cancel.set()
        if self._chat_service is not None:
            self._chat_service.cancel_current()
        self._sync_model_activity()

    def _stop_current(self) -> None:
        if self._memory_busy or self._models.job_kind() == "load":
            self._force_stop_model()
        else:
            self._chat_view.stop()

    def _on_models_unload(self) -> None:
        self._chat_view.set_session_busy(True)

    def _on_catalog_model_loaded(self, model: object) -> None:
        self._chat_view.set_session_busy(False)
        self._models.apply_loaded(model)
        self._models.finish_job()
        self._sync_model_activity()

    def _on_catalog_model_unloaded(self) -> None:
        self._chat_view.set_session_busy(False)
        self._models.apply_unloaded()
        self._models.finish_job()
        self._sync_model_activity()

    def _on_catalog_model_deleted(self) -> None:
        self._chat_view.set_session_busy(False)
        self._models.apply_unloaded()
        self._models.finish_job()
        self._models.refresh()
        self._sync_model_activity()

    def _on_catalog_model_failed(self, code: str, message: str) -> None:
        self._chat_view.set_session_busy(False)
        self._models.apply_failed(EngineError(code, message))
        self._models.finish_job()
        self._sync_model_activity()

    def _on_chat_busy_started(self, *_args: object) -> None:
        self._models.set_chat_busy(True)
        self._models.sync_from_session()
        self._memory.set_capture_available(False)

    def _on_chat_busy_ended(self, *_args: object) -> None:
        self._models.set_chat_busy(False)
        self._models.sync_from_session()
        self._sync_memory_available()
        self._sync_model_activity()

    def _on_filter(self, selection: object) -> None:
        if not isinstance(selection, SidebarSelection):
            return
        if selection.folder == FOLDER_ALL:
            self._list.set_project_filter(...)
            self._detail_stack.setCurrentWidget(self._chat_view)
        elif selection.folder == FOLDER_PROJECT:
            self._list.set_project_filter(selection.project_id, project_name=selection.project_name)
            project = next((p for p in self._library.list_projects()
                            if p.id == selection.project_id), None)
            if project is not None:
                self._project_home.set_project(project)
                self._detail_stack.setCurrentWidget(self._project_home)
                if not self._catalog_ready:
                    self._chat_view.catalog_requested.emit()
        self._sync_title()

    def _on_chat_created(self, _cid: int) -> None:
        self._show_selected_chat()
        self._project_home.refresh_chats()

    def _on_selected(self, cid: object) -> None:
        if self._private_id is not None:
            return
        self._project_home.refresh_chats()
        self._sync_memory_available()
        self._sync_title()
        if isinstance(cid, int):
            self._settings.setValue(KEY_LAST_CONVERSATION_ID, cid)
        if not isinstance(cid, int):
            self._chat_view.set_conversation(None)
            return
        if self._chat_view.is_streaming(cid) and self._chat_view.conversation_id() == cid:
            return
        if self._chat_view.keeping_error_buffer(cid) and self._chat_view.conversation_id() == cid:
            return
        getter = getattr(self._library, "get_conversation", None)
        if not callable(getter):
            self._chat_view.set_conversation(None)
            return
        self._chat_view.set_conversation(getter(cid))

    def _on_stop(self, conversation_id: int) -> None:
        self._chat_cancel.set()
        if self._chat_service is not None:
            self._chat_service.stop(conversation_id)

    def _on_model_selected(self, ref: object) -> None:
        if not isinstance(ref, ModelRef):
            return
        cid = self._chat_view.conversation_id()
        if cid is None or self._chat_service is None:
            return
        self._chat_service.set_model(cid, ref)
        self._chat_view.set_conversation(self._chat_service.get_conversation(cid))

    def _on_chat_accepted(self, cid: int, _kind: str) -> None:
        if cid < 0:
            return
        self._list.refresh(select_id=cid if self._sidebar.current_section() == CHATS
                           else self._list.selected_id())
        self._sync_title()

    def _on_turn_finished(self, cid: int) -> None:
        if cid < 0:
            return
        self._list.refresh(select_id=cid if self._sidebar.current_section() == CHATS
                           else self._list.selected_id())
        self._sync_title()

    def _sync_title(self) -> None:
        if self._sidebar.current_section() == PRIVATE:
            self.setWindowTitle(f"Private chat — No history or memories — {_TITLE}")
            return
        if self._sidebar.current_section() == SETTINGS:
            self.setWindowTitle(f"Settings — {_TITLE}")
            return
        if self._sidebar.current_section() == MEMORY:
            self.setWindowTitle(f"Second Brain — {_TITLE}")
            return
        if self._sidebar.current_section() == MODELS:
            self.setWindowTitle(f"Models — {_TITLE}")
            return
        if self._detail_stack.currentWidget() is self._project_home:
            self.setWindowTitle(f"{self._project_home.project.name} — {_TITLE}")
            return
        if self._detail_stack.currentWidget() is self._project_form:
            self.setWindowTitle(f"Create a project — {_TITLE}")
            return
        title = self._list.selected_title()
        self.setWindowTitle(title if title else _TITLE)

    def _sync_memory_available(self) -> None:
        cid = self._list.selected_id()
        available = False
        if (cid is not None and self._chat_service is not None and not self._memory_busy
                and self._private_id is None):
            try:
                conversation = self._library.get_conversation(cid)
                available = bool(conversation.messages and conversation.summary.model) and not (
                    self._session.status().generating or self._chat_service._generating
                )
            except EngineError:
                pass
        self._memory.set_capture_available(available)

    def _remember_conversation(self) -> None:
        if (self._closing or self._memory_busy or self._worker is None
                or self._private_id is not None):
            return
        cid = self._list.selected_id()
        if cid is None:
            return
        self._sidebar.select_section(MEMORY)
        self._start_memory(cid)

    def _start_memory(self, cid: int, *, automatic: bool = False) -> None:
        if cid < 0:
            return
        self._automatic_capture = automatic
        self._memory_busy = True
        self._memory_cancel = threading.Event()
        self._memory.set_busy(True)
        self._memory.set_status("Creating connected memories from this conversation…")
        self._chat_view.set_session_busy(True)
        self._models.set_chat_busy(True)
        self.memory_requested.emit(cid, self._memory_vault, self._memory_cancel)

    def _schedule_automatic_memory(
        self, cid: int, cancelled: bool, chunks: int, _elapsed: float, _tps: float,
    ) -> None:
        if (not self._closing and cid >= 0 and not cancelled and chunks > 0
                and as_bool(self._settings.value(KEY_AUTO_MEMORY, True), True)):
            self._pending_memories.add(cid)

    def _drain_automatic_memories(self) -> None:
        service = self._chat_service
        if (self._closing or not self._pending_memories or self._memory_busy
                or self._private_id is not None or service is None
                or self._worker is None or service._generating
                or self._chat_view.is_streaming() or self._chat_view._pending is not None
                or self._session.status().generating or self._models.job_kind() is not None
                or not as_bool(self._settings.value(KEY_AUTO_MEMORY, True), True)):
            return
        cid = min(self._pending_memories)
        self._pending_memories.discard(cid)
        try:
            conversation = service.get_conversation(cid)
        except EngineError:
            return
        if conversation.messages and conversation.summary.model:
            self._start_memory(cid, automatic=True)

    def _on_automatic_memory(self, enabled: bool) -> None:
        if not enabled:
            self._pending_memories.clear()
            if self._automatic_capture:
                self._cancel_memory()

    def _cancel_memory(self) -> None:
        self._memory_cancel.set()
        self._memory.set_status("Cancelling memory creation…")

    def _finish_memory(self) -> None:
        self._automatic_capture = False
        self._memory_busy = False
        self._memory.set_busy(False)
        self._chat_view.set_session_busy(False)
        self._models.set_chat_busy(False)
        self._models.sync_from_session()
        self._sync_memory_available()
        self._sync_model_activity()

    def _on_memories_created(self, result: object) -> None:
        automatic = self._automatic_capture
        self._finish_memory()
        if not isinstance(result, CaptureResult):
            return
        if result.already_saved:
            self._memory.set_status(
                "This version of the conversation is already in your Second Brain."
            )
        elif result.notes:
            count = len(result.notes)
            noun = "memory" if count == 1 else "memories"
            self._memory.set_status(
                f"Created {count} connected {noun}. You can edit them here."
            )
        else:
            self._memory.set_status("The model found no new durable memories in this conversation.")
        self._memory.refresh(select_key=result.notes[0] if result.notes and not automatic else None)

    def _on_memories_failed(self, _code: str, message: str) -> None:
        self._finish_memory()
        self._memory.set_status(message)

    def _on_memory_recall(self, enabled: bool) -> None:
        self._settings.setValue("memory/recall", enabled)
        if self._chat_service is not None:
            self._chat_service.memory_vault = self._memory_vault if enabled else None

    def _on_vault_changed(self, vault: object) -> None:
        if isinstance(vault, MemoryVault):
            self._memory_vault = vault
            self._settings.setValue("memory/vault", str(vault.root))
            self._on_memory_recall(self._memory._recall.isChecked())

    def _open_memory_source(self, cid: int) -> None:
        try:
            self._library.get_conversation(cid)
        except EngineError:
            self._memory.set_status(
                "The original conversation was deleted. Its saved source note is still available."
            )
            return
        self._sidebar.select_all()
        self._list.refresh(select_id=cid)
