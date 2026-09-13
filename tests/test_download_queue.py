from __future__ import annotations

import os
import threading
import time

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.services.discovery import RemoteModel
from llm_engine.services.downloads import DownloadChoice, DownloadPlan, HubFile


def _app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _wait(predicate):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(10)
    assert predicate()


def _plan(name):
    model = RemoteModel(f"owner/{name}", "gguf", 1, 1024, "Estimated")
    choice = DownloadChoice(f"{name}.gguf", (HubFile(f"{name}.gguf", 1024),))
    return DownloadPlan(model, "a" * 40, (choice,)), choice


def test_parallel_jobs_queue_deduplicate_cancel_and_start_next():
    app = _app()
    from llm_manager_app.widgets.downloads_view import DownloadManager, DownloadsView

    manager = DownloadManager(parallel=2)
    view = DownloadsView(manager)
    entered = {name: threading.Event() for name in ("one", "two", "three", "four")}
    release = {name: threading.Event() for name in entered}

    class Service:
        def download(self, plan, choice, cancel, progress, token):
            name = plan.model.repo_id.split("/")[1]
            entered[name].set()
            progress(512, 1024, choice.name)
            while not release[name].wait(0.01):
                if cancel.is_set():
                    raise EngineError("cancelled", "Partial files removed")
            return ModelRef(BackendName.GGUF, name)

    service = Service()
    try:
        first = manager.add(service, *_plan("one"))
        second = manager.add(service, *_plan("two"))
        third = manager.add(service, *_plan("three"))
        fourth = manager.add(service, *_plan("four"))
        assert entered["one"].wait(2) and entered["two"].wait(2)
        assert not entered["three"].is_set() and not entered["four"].is_set()
        assert manager.active_count == 4
        app.processEvents()
        assert view.summary.text() == "2 downloading · 2 queued"
        assert view._rows[third.id][1].isHidden()
        assert "owner/one" in view._rows[first.id][2].accessibleName()
        assert view._details[first.id].isHidden()
        view._disclosures[first.id].click()
        assert not view._details[first.id].isHidden()
        assert manager.add(service, *_plan("one")) is first
        assert len(manager.jobs) == 4
        manager.cancel(fourth.id)
        assert fourth.state == "Cancelled"
        manager.cancel(first.id)
        assert view._rows[first.id][1].maximum() == 0
        _wait(lambda: first.state == "Cancelled" and entered["three"].is_set())
        assert second.state == "Downloading" and third.state == "Downloading"
        release["two"].set()
        release["three"].set()
        _wait(lambda: second.state == third.state == "Complete")
        assert not entered["four"].is_set()
        manager.retry(fourth.id)
        assert entered["four"].wait(2)
        release["four"].set()
        _wait(lambda: fourth.state == "Complete")
        assert manager.active_count == 0
        assert len(view._rows) == 4
        assert view._rows[second.id][2].text() == "Open model"
        assert view._rows[second.id][1].isHidden()
        assert not view._details[first.id].isHidden()
    finally:
        assert manager.shutdown(3000)
        app.processEvents()
        view.close()


def test_find_models_from_downloads_waits_for_catalog_refresh(tmp_path, monkeypatch):
    app = _app()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QAction
    from PySide6.QtTest import QTest

    from llm_engine.backends.fake import FakeBackend
    from llm_engine.backends.registry import BackendRegistry
    from llm_engine.hardware import GIB, Hardware
    from test_models_view import _window

    window, store, _ = _window(tmp_path, BackendRegistry([FakeBackend()]))
    calls = []

    class Discovery:
        def recommend(self, hardware, format, query):
            calls.append(query)
            return [_plan("suggested")[0].model]

    window._models._discovery_service = Discovery()
    monkeypatch.setattr("llm_manager_app.widgets.models_view.detect_hardware",
                        lambda: Hardware("Linux", "x86_64", 16 * GIB))
    try:
        previous = window._detail_stack.currentWidget()
        window._download_button.click()
        assert window._downloads_popover.isVisible()
        assert window._detail_stack.currentWidget() is previous
        assert all(window._sidebar._model.item(row).text() != "Downloads"
                   for row in range(window._sidebar._model.rowCount()))
        QTest.keyClick(window._downloads_popover, Qt.Key.Key_Escape)
        assert not window._downloads_popover.isVisible()
        assert not window._download_button.isChecked()
        window._download_button.click()
        window._models.download_view.browse_requested.emit()
        assert not window._downloads_popover.isVisible()
        _wait(lambda: window._models._discovery.results.count() == 1
              and not window._models._busy)
        assert calls == [""]
        assert window._models._tabs.currentIndex() == 1
        assert window._models._advanced_details.isHidden()
        window._models._details_toggle.click()
        assert not window._models._advanced_details.isHidden()
        toggle = window.findChild(QAction, "toggleSidebarAction")
        assert toggle.text() == "Hide Sidebar"
        toggle.trigger()
        assert window._sidebar.is_collapsed() and toggle.text() == "Show Sidebar"
        window._sidebar.toggle_collapsed()
        assert not window._sidebar.is_collapsed() and toggle.text() == "Hide Sidebar"
    finally:
        window.close()
        app.processEvents()
        store.close()


def test_failure_does_not_stop_other_download_and_retry_is_independent():
    app = _app()
    from llm_manager_app.widgets.downloads_view import DownloadManager

    manager = DownloadManager(parallel=2)
    calls = []

    class Service:
        def download(self, plan, choice, cancel, progress, token):
            name = plan.model.repo_id
            calls.append(name)
            if name.endswith("one") and calls.count(name) == 1:
                raise EngineError("download_failed", "Check your connection")
            return ModelRef(BackendName.GGUF, name)

    try:
        first = manager.add(Service(), *_plan("one"))
        second = manager.add(Service(), *_plan("two"))
        _wait(lambda: first.state == "Failed" and second.state == "Complete")
        manager.retry(first.id)
        _wait(lambda: first.state == "Complete")
        assert calls.count("owner/two") == 1
    finally:
        assert manager.shutdown(3000)
        app.processEvents()


def test_closing_download_popover_keeps_transfer_running(tmp_path):
    app = _app()
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from llm_engine.backends.fake import FakeBackend
    from llm_engine.backends.registry import BackendRegistry
    from test_models_view import _window

    window, store, _ = _window(tmp_path, BackendRegistry([FakeBackend()]))
    started, release = threading.Event(), threading.Event()

    class Service:
        def download(self, plan, choice, cancel, progress, token):
            started.set()
            while not release.wait(0.01):
                if cancel.is_set():
                    raise EngineError("cancelled", "Cancelled")
            return ModelRef(BackendName.GGUF, "one")

    window._models._downloads = Service()
    try:
        window._models._download_model(*_plan("one"), "")
        assert started.wait(2)
        assert window._download_button.text() == "1"
        previous = window._detail_stack.currentWidget()
        window._show_downloads()
        QTest.keyClick(window._downloads_popover, Qt.Key.Key_Escape)
        assert window._models.downloads.active_count == 1
        assert not window._models.downloads.jobs[1].cancel.is_set()
        assert window._detail_stack.currentWidget() is previous
        window._show_downloads()
        assert window._models.download_view._rows[1][2].text() == "Cancel"
        compact_height = window._downloads_popover.height()
        window._models.download_view._disclosures[1].click()
        app.processEvents()
        assert window._downloads_popover.height() > compact_height
        release.set()
        _wait(lambda: window._models.downloads.jobs[1].state == "Complete")
        assert window._download_button.text() == "Downloads"
        assert window._models.download_view._rows[1][2].text() == "Open model"
    finally:
        release.set()
        window.close()
        app.processEvents()
        store.close()


def test_browsing_remains_available_during_two_downloads(monkeypatch):
    app = _app()
    from llm_engine.hardware import GIB, Hardware
    from llm_manager_app.widgets.models_view import ModelsView
    from test_models_view import StubCatalog

    view = ModelsView(catalog=StubCatalog([], {}))
    running = [threading.Event(), threading.Event()]
    finished = threading.Event()

    class Service:
        def download(self, plan, choice, cancel, progress, token):
            running[0 if plan.model.repo_id.endswith("one") else 1].set()
            while not finished.wait(0.01):
                if cancel.is_set():
                    raise EngineError("cancelled", "Cancelled")
            return ModelRef(BackendName.GGUF, plan.model.repo_id)

    class Discovery:
        def search(self, query, format):
            assert query == "another"
            return [_plan("another")[0].model]

    view._downloads = Service()
    view._discovery_service = Discovery()
    monkeypatch.setattr("llm_manager_app.widgets.models_view.detect_hardware",
                        lambda: Hardware("Linux", "x86_64", 16 * GIB))
    try:
        view._download_model(*_plan("one"), "")
        view._download_model(*_plan("two"), "")
        assert all(event.wait(2) for event in running)
        assert view._discovery.results.isEnabled()
        view._discover("another", "gguf", False)
        _wait(lambda: view._discovery.results.count() == 1 and not view._busy)
        assert view._discovery.results.item(0).text() == "Another"
        assert view.downloads.active_count == 2
    finally:
        finished.set()
        assert view.shutdown(3000)
        app.processEvents()
        view.close()
