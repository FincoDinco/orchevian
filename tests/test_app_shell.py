from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_manager_app.tokens import DARK, LIGHT, named_tab_width, qcolor, qss

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = ROOT / "src" / "llm_manager_app"
BANNED_ROOTS = frozenset(
    {
        "sqlite3",
        "mlx_lm",
        "llama_cpp",
        "fastapi",
        "huggingface_hub",
        "pyqt_liquidglass",
    }
)


def _top_level(name: str) -> str:
    return name.split(".")[0]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(_top_level(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(_top_level(node.module))
    return roots


def test_app_sources_do_not_import_sqlite3_or_mlx_lm() -> None:
    offenders: list[str] = []
    for path in APP_SRC.rglob("*.py"):
        for name in sorted(_imported_roots(path) & BANNED_ROOTS):
            offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert offenders == []


@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_studio_text_has_readable_contrast(palette) -> None:
    def luminance(value: str) -> float:
        color = qcolor(value)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    for foreground in (palette.text, palette.secondary):
        for background in (palette.canvas, palette.sidebar, palette.elevated):
            light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
            assert (light + 0.05) / (dark + 0.05) >= 4.5


def test_named_tab_width_is_one_tab_not_the_pane() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QWidget

    host = QWidget()
    host.setFont(app.font())
    chats = named_tab_width(host, ("Chats",))
    models = named_tab_width(host, ("Models",))
    both = named_tab_width(host, ("Chats", "Models"))
    assert both == max(chats, models)
    assert both < 160


def test_qcolor_parses_studio_rgba_selection() -> None:
    color = qcolor("rgba(91, 141, 239, 56)")
    assert color.isValid()
    assert color.red() == 91
    assert color.green() == 141
    assert color.blue() == 239
    assert color.alpha() == 56
    assert qcolor(DARK.accent).name().upper() == DARK.accent


def test_studio_qss_is_small_and_uses_named_colors() -> None:
    sheet = qss(DARK)
    assert len(sheet) < 16_000
    assert DARK.canvas in sheet
    assert DARK.selection in sheet
    assert DARK.danger in sheet
    assert "modelsError" in sheet
    assert "modelsBanner" in sheet
    assert "border-left" not in sheet
    assert "border-right" not in sheet
    assert "QSplitter::handle" in sheet
    assert "brass" not in sheet.lower()
    assert "liquid" not in sheet.lower()
    assert "NSVisualEffectView" not in sheet


def _qapp():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["orchevian-tests"])
    return app


def _library(tmp_path: Path):
    from llm_engine.store.library import LibraryService
    from llm_engine.store.sqlite import SqliteStore

    store = SqliteStore(tmp_path / "data.db")
    return store, LibraryService(store)


def _window(tmp_path: Path, registry: BackendRegistry | None = None, library=None):
    from PySide6.QtCore import QSettings

    from llm_manager_app.main_window import MainWindow

    store = None
    if library is None:
        store, library = _library(tmp_path)
    if registry is None:
        registry = BackendRegistry([FakeBackend()])
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    window = MainWindow(registry=registry, library=library, settings=settings)
    return window, store, library


def test_main_window_unified_sidebar_and_workspace(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QLabel, QListView, QSplitter, QStackedWidget, QToolBar

    from llm_manager_app.widgets.chat_view import ChatView
    from llm_manager_app.widgets.conversation_list import ConversationList
    from llm_manager_app.widgets.sidebar import Sidebar

    class _NoList(FakeBackend):
        def list_models(self):
            raise AssertionError("list_models must not run at window startup")

    registry = BackendRegistry([_NoList()])
    window, store, _library_svc = _window(tmp_path, registry=registry)
    try:
        splitter = window.findChild(QSplitter)
        assert splitter is not None
        assert splitter.count() == 2
        sidebar = splitter.widget(0)
        workspace = splitter.widget(1)
        detail = workspace.findChild(QStackedWidget, "detailPane")
        assert isinstance(sidebar, Sidebar)
        assert sidebar.findChild(ConversationList) is window._list
        view = sidebar.findChild(QListView, "conversationView")
        assert view is not None
        model = view.model()
        assert model is not None and model.rowCount() == 0
        assert isinstance(detail, QStackedWidget)
        assert isinstance(detail.currentWidget(), ChatView)
        empty = detail.findChild(QLabel, "chatEmpty")
        assert empty is not None
        assert "Select a conversation" in empty.text()
        assert window.windowTitle() == "Orchevian"
        assert window.minimumWidth() >= 1024
        assert window.minimumHeight() >= 680
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
        window.show()
        app.processEvents()
        toolbar = workspace.findChild(QToolBar, "workspaceToolbar")
        assert toolbar is not None
        assert window.toolBarArea(toolbar) == Qt.ToolBarArea.NoToolBarArea
        assert sidebar.mapTo(window, QPoint()).y() == workspace.mapTo(window, QPoint()).y()
    finally:
        window.close()
        if store is not None:
            store.close()


def test_welcome_creates_a_conversation(tmp_path: Path) -> None:
    app = _qapp()
    from PySide6.QtWidgets import QPushButton

    window, store, library = _window(tmp_path)
    try:
        window.show()
        app.processEvents()
        button = window.findChild(QPushButton, "welcomeNewChat")
        assert button is not None and button.isVisible()
        button.click()
        app.processEvents()
        rows = library.list_conversations()
        assert len(rows) == 1
        assert window._chat_view.conversation_id() == rows[0].id
        assert window._chat_view.composer().isVisible()
    finally:
        window.close()
        store.close()


def test_starter_places_a_draft_without_sending(tmp_path: Path) -> None:
    app = _qapp()
    from PySide6.QtWidgets import QPushButton

    from llm_engine.domain.models import BackendName, ModelRef

    window, store, library = _window(tmp_path)
    try:
        cid = library.create_conversation(model=ModelRef(BackendName.OLLAMA, "fake")).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        starter = window._chat_view._starters.findChild(QPushButton, "starterButton")
        assert starter is not None and starter.isVisible()
        starter.click()
        assert window._chat_view.composer().text() == "Help me think through an idea: "
        assert library.get_conversation(cid).messages == ()
        assert not window._chat_view.is_streaming()
    finally:
        window.close()
        store.close()


def test_sidebar_chat_opens_from_models_and_find_expands_sidebar(tmp_path: Path) -> None:
    app = _qapp()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window, store, library = _window(tmp_path)
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        window._sidebar.select_section("models")
        app.processEvents()
        view = window._list._view
        index = window._list._model.index_for_id(cid)
        QTest.mouseClick(
            view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualRect(index).center()
        )
        app.processEvents()
        assert window._sidebar.current_section() == "chats"
        assert window._chat_view.conversation_id() == cid
        assert window._detail_stack.currentWidget() is window._chat_view
        window._sidebar.set_collapsed(True)
        window._focus_search()
        app.processEvents()
        assert window._list._search.isVisible()
        assert window._list._search.hasFocus()
    finally:
        window.close()
        store.close()


def test_sidebar_chats_and_models(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_manager_app.widgets.sidebar import MODELS

    window, store, _library_svc = _window(tmp_path)
    try:
        sidebar = window._sidebar
        assert sidebar.current_section() == "chats"
        seen: list[str] = []
        sidebar.section_changed.connect(seen.append)
        sidebar.select_section(MODELS)
        assert sidebar.current_section() == "models"
        assert seen == ["models"]
        assert window.windowTitle() == "Models — Orchevian"
    finally:
        window.close()
        if store is not None:
            store.close()


def test_sidebar_collapses_to_named_tab_width(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QToolButton, QTreeView

    window, store, _library_svc = _window(tmp_path)
    try:
        window.show()
        app.processEvents()
        sidebar = window._sidebar
        sidebar.new_project(name="Work")
        tree = window.findChild(QTreeView, "sidebarNav")
        btn = window.findChild(QToolButton, "sidebarCollapse")
        assert tree is not None and btn is not None
        assert not sidebar.is_collapsed()
        rail = sidebar.tab_width()
        assert rail < 160
        expanded = window._splitter.sizes()[0]
        assert expanded > rail
        chats = sidebar._chats_item
        assert chats is not None
        parent = chats.index()
        assert not tree.isRowHidden(0, parent)
        btn.click()
        app.processEvents()
        assert sidebar.is_collapsed()
        assert window._splitter.sizes()[0] == rail
        assert sidebar.maximumWidth() == rail
        assert tree.isVisible()
        assert tree.isRowHidden(0, parent)
        btn.click()
        app.processEvents()
        assert not sidebar.is_collapsed()
        assert not tree.isRowHidden(0, parent)
        assert window._splitter.sizes()[0] >= 200
        assert sidebar.maximumWidth() > rail
    finally:
        window.close()
        if store is not None:
            store.close()


def test_inspector_hidden_by_default_and_reopens_without_losing_prompt(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit, QToolButton

    window, store, library = _window(tmp_path)
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        view = window._chat_view
        inspector = view.inspector()
        assert not view.inspector_open()
        assert not inspector.isVisible()
        assert view._chat_split.sizes()[1] == 0
        view.set_inspector_open(True)
        app.processEvents()
        prompt = inspector.findChild(QPlainTextEdit, "systemPromptEdit")
        btn = inspector.findChild(QToolButton, "inspectorCollapse")
        assert prompt is not None and btn is not None
        assert view.inspector_open()
        assert prompt.isVisible()
        expanded_min = inspector.minimumWidth()
        assert expanded_min >= 280
        prompt.setPlainText("Keep answers concise.")
        btn.click()
        app.processEvents()
        rail = inspector.tab_width()
        assert rail < 180
        assert not view.inspector_open()
        assert not inspector.isVisible()
        assert not prompt.isVisible()
        assert inspector.maximumWidth() == rail
        sizes = view._chat_split.sizes()
        assert sizes[1] == 0
        view.set_inspector_open(True)
        app.processEvents()
        assert view.inspector_open()
        assert prompt.isVisible()
        assert prompt.toPlainText() == "Keep answers concise."
        assert inspector.maximumWidth() > rail
        assert inspector.minimumWidth() == expanded_min
    finally:
        window.close()
        if store is not None:
            store.close()


def test_importing_app_does_not_load_banned_modules() -> None:
    pytest.importorskip("PySide6")
    banned = ("sqlite3", "mlx_lm", "llama_cpp", "fastapi", "huggingface_hub")
    before = {name for name in banned if name in sys.modules}
    import llm_manager_app.main_window as app_main

    loaded = [name for name in banned if name in sys.modules and name not in before]
    assert loaded == []
    assert app_main.MainWindow is not None


def test_app_sources_do_not_call_stream_generate() -> None:
    offenders: list[str] = []
    for path in APP_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "stream_generate" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_conversation_list_uses_summaries_not_messages(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QLineEdit, QListView, QPushButton

    store, library = _library(tmp_path)
    secret = "UNIQUE_MESSAGE_BODY_SHOULD_NOT_APPEAR_IN_LIST"

    class Guard:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_conversations(self, project_id=..., query: str | None = None):
            self.calls.append("list_conversations")
            return library.list_conversations(project_id=project_id, query=query)

        def get_conversation(self, id: int):
            self.calls.append("get_conversation")
            return library.get_conversation(id)

        def create_conversation(self, project_id: int | None = None, model=None):
            self.calls.append("create_conversation")
            return library.create_conversation(project_id=project_id, model=model)

        def rename(self, id: int, title: str) -> None:
            self.calls.append("rename")
            library.rename(id, title)

        def delete_conversation(self, id: int) -> None:
            self.calls.append("delete_conversation")
            library.delete_conversation(id)

        def move(self, id: int, project_id: int | None) -> None:
            self.calls.append("move")
            library.move(id, project_id)

        def list_projects(self):
            self.calls.append("list_projects")
            return library.list_projects()

        def create_project(self, name: str, instructions: str = "", model=None):
            self.calls.append("create_project")
            return library.create_project(name, instructions, model)

        def delete_project(self, id: int) -> None:
            self.calls.append("delete_project")
            library.delete_project(id)

    guard = Guard()
    created = library.create_conversation()
    store.add_message(created.summary.id, "user", secret)
    window, _owned, _svc = _window(tmp_path, library=guard)
    try:
        window.show()
        assert "list_conversations" in guard.calls
        view = window.findChild(QListView, "conversationView")
        assert view is not None
        model = view.model()
        assert model is not None
        assert model.rowCount() == 1
        assert model.data(model.index(0, 0)) == "New Chat"
        assert secret not in (model.data(model.index(0, 0)) or "")
        assert window.windowTitle() == "New Chat"

        search = window.findChild(QLineEdit, "conversationSearch")
        assert search is not None
        search.setText("nope")
        assert model.rowCount() == 0
        empty = window.findChild(QLabel, "listEmpty")
        assert empty is not None
        assert "No matching conversations" in empty.text()

        search.clear()
        btn = window.findChild(QPushButton, "newChatButton")
        assert btn is not None
        btn.click()
        assert "create_conversation" in guard.calls
        assert model.rowCount() == 2
        list_calls = [c for c in guard.calls if c == "list_conversations"]
        assert list_calls
    finally:
        window.close()
        store.close()


def test_conversation_crud_search_rename_delete_and_empty(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWidgets import QLabel, QLineEdit, QListView

    window, store, library = _window(tmp_path)
    try:
        window.show()
        pane = window._list
        empty = window.findChild(QLabel, "listEmpty")
        assert empty is not None
        assert "No conversations" in empty.text()
        assert window.windowTitle() == "Orchevian"

        first = pane.new_chat()
        pane.rename_selected("Alpha notes")
        second = pane.new_chat()
        pane.rename_selected("Beta work")
        assert {row.title for row in library.list_conversations()} == {
            "Alpha notes",
            "Beta work",
        }
        view = window.findChild(QListView, "conversationView")
        assert view is not None
        model = view.model()
        assert model is not None
        assert model.rowCount() == 2
        assert window.windowTitle() == "Beta work"

        search = window.findChild(QLineEdit, "conversationSearch")
        assert search is not None
        search.setText("alpha")
        assert model.rowCount() == 1
        assert model.data(model.index(0, 0)) == "Alpha notes"
        pane.select_id(first)
        assert window.windowTitle() == "Alpha notes"

        search.clear()
        pane.select_id(second)
        pane.delete_selected(confirmed=True)
        titles = {row.title for row in library.list_conversations()}
        assert titles == {"Alpha notes"}
        assert pane.selected_id() == first
        assert window.windowTitle() == "Alpha notes"

        pane.delete_selected(confirmed=True)
        assert library.list_conversations() == []
        assert "No conversations" in empty.text()
        assert window.windowTitle() == "Orchevian"

        actions = window.findChildren(QAction)
        keys = [a.shortcut() for a in actions]
        assert any(k.matches(QKeySequence(QKeySequence.StandardKey.New)) for k in keys)
        assert any(k.matches(QKeySequence(QKeySequence.StandardKey.Find)) for k in keys)
        assert any(k.matches(QKeySequence("Ctrl+Shift+N")) for k in keys)
        assert any(k.matches(QKeySequence("Ctrl+1")) for k in keys)
        assert any(k.matches(QKeySequence("Ctrl+2")) for k in keys)
        assert any(k.matches(QKeySequence("Ctrl+L")) for k in keys)
        action_titles = {a.text().replace("&", "") for a in actions}
        assert "New Chat" in action_titles
        assert "New Project" in action_titles
        assert "Models" in action_titles
        assert "Delete Conversation" in action_titles
    finally:
        window.close()
        if store is not None:
            store.close()


def test_new_chat_shortcut_switches_to_chats(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_manager_app.widgets.sidebar import MODELS

    window, store, _library_svc = _window(tmp_path)
    try:
        window._sidebar.select_section(MODELS)
        assert window.windowTitle() == "Models — Orchevian"
        window._new_chat_action.trigger()
        assert window._sidebar.current_section() == "chats"
        assert window._list.selected_title() == "New Chat"
        assert window.windowTitle() == "New Chat"
    finally:
        window.close()
        if store is not None:
            store.close()


def test_find_targets_current_workspace_and_composer_noops_on_models(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    window, store, _library_svc = _window(tmp_path)
    try:
        window.show()
        window._sidebar.select_section("models")
        focused: list[str] = []
        window._models.focus_search = lambda: focused.append("models")
        window._list.focus_search = lambda: focused.append("find")  # type: ignore[method-assign]
        window._chat_view.focus_composer = lambda: focused.append("composer")  # type: ignore[method-assign]
        window._focus_search()
        window._focus_composer()
        assert focused == ["models"]
        window._sidebar.select_section("chats")
        window._focus_search()
        window._focus_composer()
        assert focused == ["models", "find", "composer"]
    finally:
        window.close()
        if store is not None:
            store.close()


def _sidebar_labels(sidebar) -> list[str]:
    from PySide6.QtCore import QModelIndex

    model = sidebar._view.model()
    labels: list[str] = []

    def walk(parent: QModelIndex) -> None:
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            labels.append(str(index.data() or ""))
            walk(index)

    walk(QModelIndex())
    return labels


def _list_titles(window) -> list[str]:
    from PySide6.QtWidgets import QListView

    view = window.findChild(QListView, "conversationView")
    assert view is not None
    model = view.model()
    assert model is not None
    return [str(model.data(model.index(row, 0)) or "") for row in range(model.rowCount())]


def test_sidebar_project_folders_hide_templates(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QTreeView

    window, store, library = _window(tmp_path)
    try:
        window.show()
        tree = window.findChild(QTreeView, "sidebarNav")
        assert tree is not None
        labels = _sidebar_labels(window._sidebar)
        assert labels == [
            "Chats", "Models", "Second Brain", "Projects"
        ]
        assert "Templates" not in labels
        window._sidebar.new_project(name="Work")
        labels = _sidebar_labels(window._sidebar)
        assert labels == [
            "Chats", "Models", "Second Brain", "Projects", "Work"
        ]
        assert window._sidebar.current_selection().project_id == library.list_projects()[0].id
    finally:
        window.close()
        if store is not None:
            store.close()


def test_project_sheet_selects_downloaded_model() -> None:
    _qapp()
    from PySide6.QtWidgets import QComboBox

    from llm_engine.domain.models import BackendName, LocalModel, ModelRef
    from llm_manager_app.widgets.project_sheet import ProjectSheet

    ref = ModelRef(BackendName.OLLAMA, "qwen3:8b")
    sheet = ProjectSheet()
    accepted = []
    sheet.accepted.connect(lambda: accepted.append(sheet.values()))
    try:
        sheet.set_catalog([LocalModel(ref, None, 1)], {"ollama": (True, None)})
        sheet._name.setText("Coding")
        sheet._instructions.setPlainText("Explain things simply.")
        combo = sheet.findChild(QComboBox, "projectModel")
        assert combo is not None and not combo.isEditable()
        assert combo.itemText(0) == "Use app default"
        assert "Ollama" in [combo.itemText(i) for i in range(combo.count())]
        combo.setCurrentIndex(combo.findData(ref))
        assert combo.currentText() == "Qwen 3 · 8B"
        assert sheet.values() == ("Coding", "Explain things simply.", ref)
        sheet.accept()
        assert len(accepted) == 1
    finally:
        sheet.close()


def test_project_sheet_optional_model_and_blank_name(tmp_path: Path) -> None:
    _qapp()
    from llm_manager_app.widgets.project_sheet import ProjectSheet

    sheet = ProjectSheet()
    accepted = []
    sheet.accepted.connect(lambda: accepted.append(sheet.values()))
    try:
        sheet.set_catalog([], {})
        sheet._name.setText("   ")
        assert not sheet._ok.isEnabled()
        sheet.accept()
        assert accepted == []
        sheet._name.setText("Work")
        assert sheet._ok.isEnabled()
        sheet.accept()
        assert len(accepted) == 1
        assert sheet.values() == ("Work", "", None)
    finally:
        sheet.close()

    window, store, library = _window(tmp_path)
    try:
        assert window._sidebar.new_project(name="   ") is None
        assert library.list_projects() == []
    finally:
        window.close()
        if store is not None:
            store.close()


def test_new_chat_seeds_selected_project(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel

    from llm_engine.domain.models import BackendName, ModelRef

    window, store, library = _window(tmp_path)
    try:
        window.show()
        model = ModelRef(BackendName.OLLAMA, "qwen3:8b")
        pid = window._sidebar.new_project(
            name="Coding",
            instructions="You are terse.",
            model=model,
        )
        assert pid is not None
        empty = window.findChild(QLabel, "listEmpty")
        assert empty is not None
        assert empty.text() == "New Chat in Coding"
        cid = window._list.new_chat()
        loaded = library.get_conversation(cid)
        assert loaded.summary.title == "New Chat"
        assert loaded.system_prompt == "You are terse."
        assert loaded.summary.model == model
        assert loaded.summary.project_id == pid
        assert _list_titles(window) == ["New Chat"]
        ungrouped = library.create_conversation()
        library.rename(ungrouped.summary.id, "Loose")
        window._sidebar.select_all()
        assert set(_list_titles(window)) == {"New Chat", "Loose"}
        window._sidebar.select_project(pid)
        assert _list_titles(window) == ["New Chat"]
    finally:
        window.close()
        if store is not None:
            store.close()


def test_conversation_list_filters_all_project_ungrouped(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    window, store, library = _window(tmp_path)
    try:
        window.show()
        work = library.create_project("Work")
        home = library.create_project("Home")
        window._sidebar.refresh()
        in_work = library.create_conversation(project_id=work.id)
        library.rename(in_work.summary.id, "Work chat")
        in_home = library.create_conversation(project_id=home.id)
        library.rename(in_home.summary.id, "Home chat")
        loose = library.create_conversation()
        library.rename(loose.summary.id, "Loose")

        window._sidebar.select_project(work.id)
        assert _list_titles(window) == ["Work chat"]

        window._sidebar.select_all()
        assert set(_list_titles(window)) == {"Work chat", "Home chat", "Loose"}

        window._sidebar.select_project(home.id)
        assert _list_titles(window) == ["Home chat"]

        window._list.set_project_filter(None)
        assert _list_titles(window) == ["Loose"]
    finally:
        window.close()
        if store is not None:
            store.close()


def test_move_conversation_and_delete_project_keeps_chats(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    window, store, library = _window(tmp_path)
    try:
        window.show()
        work = library.create_project("Work")
        home = library.create_project("Home")
        window._sidebar.refresh()
        chat = library.create_conversation(project_id=work.id)
        library.rename(chat.summary.id, "Moving")
        window._sidebar.select_project(work.id)
        assert _list_titles(window) == ["Moving"]

        window._list.move_selected(home.id)
        assert library.get_conversation(chat.summary.id).summary.project_id == home.id
        assert _list_titles(window) == []
        window._sidebar.select_project(home.id)
        assert _list_titles(window) == ["Moving"]

        window._sidebar.select_project(home.id)
        window._sidebar.delete_selected_project(confirmed=False)
        assert {p.id for p in library.list_projects()} == {home.id, work.id}

        window._sidebar.delete_selected_project(confirmed=True)
        assert [p.name for p in library.list_projects()] == ["Work"]
        loaded = library.get_conversation(chat.summary.id)
        assert loaded.summary.project_id is None
        assert loaded.summary.title == "Moving"
        window._list.set_project_filter(None)
        assert _list_titles(window) == ["Moving"]
        labels = _sidebar_labels(window._sidebar)
        assert "Home" not in labels
        assert "Work" in labels
    finally:
        window.close()
        if store is not None:
            store.close()


def test_new_chat_from_models_does_not_seed_last_project(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_engine.domain.models import BackendName, ModelRef
    from llm_manager_app.widgets.sidebar import MODELS

    window, store, library = _window(tmp_path)
    try:
        window.show()
        window._sidebar.new_project(
            name="Coding",
            instructions="You are terse.",
            model=ModelRef(BackendName.OLLAMA, "qwen3:8b"),
        )
        window._sidebar.select_section(MODELS)
        window._new_chat_action.trigger()
        cid = window._list.selected_id()
        assert cid is not None
        loaded = library.get_conversation(cid)
        assert loaded.summary.project_id is None
        assert loaded.system_prompt == ""
        assert window._sidebar.current_section() == "chats"
    finally:
        window.close()
        if store is not None:
            store.close()


def test_sidebar_left_from_project_keeps_folder(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    window, store, _library = _window(tmp_path)
    try:
        window.show()
        pid = window._sidebar.new_project(name="Work")
        assert pid is not None
        window._sidebar.select_project(pid)
        previous = window._sidebar._view.currentIndex()
        chats = window._sidebar._chats_item
        assert chats is not None
        window._sidebar._on_current_changed(chats.index(), previous)
        assert window._sidebar.current_selection().project_id == pid
    finally:
        window.close()
        if store is not None:
            store.close()
