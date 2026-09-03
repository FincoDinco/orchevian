from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_manager_app.tokens import DARK, LIGHT, qss

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


def test_studio_dark_tokens_match_design() -> None:
    assert DARK.canvas == "#1C1C1E"
    assert DARK.elevated == "#2C2C2E"
    assert DARK.text == "#F5F5F7"
    assert DARK.secondary == "#8E8E93"
    assert DARK.accent == "#5B8DEF"
    assert DARK.danger == "#FF453A"
    assert DARK.radius_control == 8
    assert DARK.radius_composer == 10


def test_studio_light_tokens_match_design() -> None:
    assert LIGHT.canvas == "#F2F2F7"
    assert LIGHT.elevated == "#FFFFFF"
    assert LIGHT.text == "#1C1C1E"
    assert LIGHT.secondary == "#6C6C70"
    assert LIGHT.accent == "#3B6FDB"
    assert LIGHT.danger == "#FF3B30"


def test_studio_qss_is_small_and_uses_named_colors() -> None:
    sheet = qss(DARK)
    assert len(sheet.splitlines()) < 80
    assert DARK.canvas in sheet
    assert DARK.selection in sheet
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
        app = QApplication(["llm-manager-tests"])
    return app


def _library(tmp_path: Path):
    from llm_engine.store.library import LibraryService
    from llm_engine.store.sqlite import SqliteStore

    store = SqliteStore(tmp_path / "data.db")
    return store, LibraryService(store)


def _window(tmp_path: Path, registry: BackendRegistry | None = None, library=None):
    from llm_manager_app.main_window import MainWindow

    store = None
    if library is None:
        store, library = _library(tmp_path)
    if registry is None:
        registry = BackendRegistry([FakeBackend()])
    window = MainWindow(registry=registry, library=library)
    return window, store, library


def test_main_window_three_column_splitter(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QListView, QSplitter

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
        assert splitter.count() == 3
        sidebar = splitter.widget(0)
        list_pane = splitter.widget(1)
        detail = splitter.widget(2)
        assert isinstance(sidebar, Sidebar)
        assert isinstance(list_pane, ConversationList)
        view = list_pane.findChild(QListView, "conversationView")
        assert view is not None
        model = view.model()
        assert model is not None and model.rowCount() == 0
        assert isinstance(detail, ChatView)
        empty = detail.findChild(QLabel, "chatEmpty")
        assert empty is not None
        assert "Select a conversation" in empty.text()
        assert window.windowTitle() == "LLM Manager"
        assert window.minimumWidth() >= 1024
        assert window.minimumHeight() >= 680
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    finally:
        window.close()
        if store is not None:
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
        assert window.windowTitle() == "Models — LLM Manager"
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

        def list_conversations(self, query: str | None = None):
            self.calls.append("list_conversations")
            return library.list_conversations(query=query)

        def get_conversation(self, id: int):
            self.calls.append("get_conversation")
            return library.get_conversation(id)

        def create_conversation(self):
            self.calls.append("create_conversation")
            return library.create_conversation()

        def rename(self, id: int, title: str) -> None:
            self.calls.append("rename")
            library.rename(id, title)

        def delete_conversation(self, id: int) -> None:
            self.calls.append("delete_conversation")
            library.delete_conversation(id)

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

    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QLabel, QLineEdit, QListView

    window, store, library = _window(tmp_path)
    try:
        window.show()
        pane = window._list
        empty = window.findChild(QLabel, "listEmpty")
        assert empty is not None
        assert "No conversations" in empty.text()
        assert window.windowTitle() == "LLM Manager"

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
        assert window.windowTitle() == "LLM Manager"

        shortcuts = window.findChildren(QShortcut)
        keys = [s.key() for s in shortcuts]
        assert any(k.matches(QKeySequence(QKeySequence.StandardKey.New)) for k in keys)
        assert any(k.matches(QKeySequence(QKeySequence.StandardKey.Find)) for k in keys)
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
        assert window.windowTitle() == "Models — LLM Manager"
        window._shortcut_new.activated.emit()
        assert window._sidebar.current_section() == "chats"
        assert window._list.selected_title() == "New Chat"
        assert window.windowTitle() == "New Chat"
    finally:
        window.close()
        if store is not None:
            store.close()
