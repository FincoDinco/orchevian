from __future__ import annotations

import os
import threading
import time

import pytest

from llm_engine.backends.registry import BackendRegistry
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from test_process_backend import make_runtime, model


def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def wait_until(predicate):
    app = qapp()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("UI did not recover")


@pytest.mark.parametrize("control", ["button", "escape", "global"])
def test_stop_load_keeps_gui_responsive_and_allows_retry(tmp_path, control):
    app = qapp()
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtTest import QTest

    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.widgets.sidebar import CHATS, MODELS

    backend, _, marker = make_runtime(tmp_path)
    registry = BackendRegistry([backend])
    store = SqliteStore(tmp_path / "data.db")
    window = MainWindow(
        registry=registry, library=LibraryService(store),
        settings=QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat),
    )
    beats = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start()
    try:
        window.show()
        window._sidebar.select_section(MODELS)
        wait_until(lambda: bool(window._models._models_by_id))
        window._models.select_id(model("stuck").ref.id)
        window._models._load_btn.click()
        wait_until(marker.exists)
        wait_until(lambda: window._force_stop.isVisible())
        assert not window.statusBar().isVisible()
        assert window._workspace_toolbar.isAncestorOf(window._force_stop)
        assert "Loading" in window._model_activity.label.text()
        assert window._models._stop_btn.isVisible()
        before = len(beats)
        wait_until(lambda: len(beats) >= before + 3)
        if control == "button":
            window._models._stop_btn.click()
        elif control == "escape":
            QTest.keyClick(window, Qt.Key.Key_Escape)
        else:
            window._sidebar.select_section(CHATS)
            assert window._force_stop.isEnabled(), (
                window._stop_requested, window._model_activity.isEnabled(),
                window._workspace_toolbar.isEnabled(), window._session.status(),
            )
            window._force_stop.click()
            assert window._stop_requested
            assert window._model_load_cancel.is_set()
        wait_until(lambda: window._models.job_kind() is None)
        assert not window._session.status().generating
        assert not window._chat_service._generating
        assert backend._process is None
        assert window._models._load_btn.isEnabled()
        assert "stopped" in window._models._error.text().lower()
        window._sidebar.select_section(MODELS)
        wait_until(lambda: window._models.job_kind() is None)
        window._models.select_id(model("good").ref.id)
        window._models._load_btn.click()
        wait_until(lambda: window._models.job_kind() is None)
        assert window._session.status().loaded.ref == model("good").ref
        window._models._unload_btn.click()
        wait_until(lambda: window._models.job_kind() is None)
        assert backend._process is None
    finally:
        timer.stop()
        window._force_stop_model()
        window.close()
        app.processEvents()
        registry.close()
        store.close()


def test_cancelled_queued_catalog_request_reports_terminal_signal(tmp_path):
    qapp()
    from llm_engine.services.chat import ChatService
    from llm_manager_app.workers import ChatWorker

    backend, session, marker = make_runtime(tmp_path)
    store = SqliteStore(tmp_path / "data.db")
    worker = ChatWorker(ChatService(LibraryService(store), session), session)
    errors = []
    worker.catalog_failed.connect(lambda code, message: errors.append(code))
    cancel = threading.Event()
    cancel.set()
    try:
        worker.catalog_load(model("stuck").ref, cancel)
        assert errors == ["cancelled"]
        assert backend._process is None
        assert not marker.exists()
    finally:
        backend.close()
        store.close()


@pytest.mark.parametrize("operation", ["send", "regenerate"])
def test_cancelled_queued_chat_request_preserves_conversation(tmp_path, operation):
    qapp()
    from llm_engine.services.chat import ChatService
    from llm_manager_app.workers import ChatWorker

    backend, session, marker = make_runtime(tmp_path)
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    cid = library.create_conversation(model=model("stuck").ref).summary.id
    store.add_message(cid, "user", "Original message")
    store.add_message(cid, "assistant", "Original answer")
    original = library.get_conversation(cid)
    worker = ChatWorker(ChatService(library, session), session)
    errors = []
    worker.rejected.connect(lambda cid, code, message: errors.append(code))
    cancel = threading.Event()
    cancel.set()
    try:
        if operation == "send":
            worker.send(cid, "Keep this as a draft", None, cancel)
        else:
            worker.regenerate(cid, None, cancel)
        assert errors == ["cancelled"]
        assert library.get_conversation(cid) == original
        assert backend._process is None
        assert not marker.exists()
    finally:
        backend.close()
        store.close()
