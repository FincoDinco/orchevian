from __future__ import annotations

import json
import os
import threading
import time

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from llm_engine.store.vault import MemoryVault


def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def wait_until(predicate):
    app = qapp()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for memory worker")


def test_edit_follow_backlinks_and_search_preserve_drafts(tmp_path):
    app = qapp()
    from llm_manager_app.widgets.memory_view import MemoryView

    vault = MemoryVault(tmp_path)
    alpha = vault.create("Alpha", "[[Beta]]")
    beta = vault.create("Beta", "The second note")
    view = MemoryView(vault)
    view.resize(1000, 750)
    view.show()
    try:
        view.open_note(alpha.key)
        view._tabs.setCurrentIndex(1)
        view._editor.setPlainText(alpha.content + "\nAn unsaved idea.")
        view.open_note(beta.key)
        assert "← Alpha" in view._connections.item(0).text()
        view._search.setText("Alpha")
        assert view._list.count() == 1
        assert "An unsaved idea." in view._editor.toPlainText()
        assert view._save.isEnabled()
        assert view.save_note()
        assert "An unsaved idea." in vault.read(alpha.key).content
        view._tabs.setCurrentIndex(2)
        app.processEvents()
        assert set(view._graph._nodes) == {alpha.key, beta.key}
        view._graph.note_activated.emit(beta.key)
        assert view._active.key == beta.key
        assert view._tabs.currentIndex() == 0
    finally:
        view.close()


def test_editor_reports_external_conflict_and_keeps_draft(tmp_path):
    qapp()
    from llm_manager_app.widgets.memory_view import MemoryView

    vault = MemoryVault(tmp_path)
    note = vault.create("Shared")
    view = MemoryView(vault)
    view._editor.setPlainText("My unsaved text")
    (tmp_path / f"{note.key}.md").write_text("External text", encoding="utf-8")
    view.refresh()
    assert view._editor.toPlainText() == "My unsaved text"
    assert not view.save_note()
    assert "changed on disk" in view._status.text()
    assert vault.read(note.key).content == "External text"
    view.close()


def test_note_reader_links_do_not_render_raw_html_or_code_as_links(tmp_path):
    qapp()
    from llm_manager_app.widgets.memory_view import MemoryView

    vault = MemoryVault(tmp_path)
    vault.create(
        "Alpha", '[[Beta|A linked idea]]\n\n`[[Code]]`\n\n<img src="file:///private/file">'
        '\n\n> An evidence quote.\n> <img src="file:///private/quoted-file">'
    )
    view = MemoryView(vault)
    rendered = view._reader.toHtml()
    assert 'href="memory:Beta"' in rendered
    assert 'href="memory:Code"' not in rendered
    assert '<img src="file:' not in rendered
    assert "> An evidence quote." not in view._reader.toPlainText()
    assert "An evidence quote." in view._reader.toPlainText()
    view.close()


def test_unsaved_note_can_cancel_window_close(tmp_path, monkeypatch):
    app = qapp()
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMessageBox

    from llm_manager_app.main_window import MainWindow

    vault = MemoryVault(tmp_path / "brain")
    vault.create("Draft")
    store = SqliteStore(tmp_path / "data.db")
    registry = BackendRegistry([FakeBackend()])
    window = MainWindow(
        registry=registry,
        library=LibraryService(store),
        memory_vault=vault,
        settings=QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat),
    )
    window.show()
    window._memory._editor.setPlainText("Keep this draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    window.close()
    app.processEvents()
    assert window.isVisible()
    assert not window._closing
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    window.close()
    assert vault.read("Notes/Draft").content == "Keep this draft"
    registry.close()
    store.close()


def test_ai_memory_capture_is_integrated_with_navigation_and_source(tmp_path):
    app = qapp()
    from PySide6.QtCore import QSettings

    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.widgets.sidebar import MEMORY

    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    cid = library.create_conversation(model=ModelRef(BackendName.OLLAMA, "fake")).summary.id
    store.add_message(cid, "user", "I prefer local tools.")
    payload = {
        "notes": [
            {
                "title": "Local tools",
                "body": "The user prefers local tools.",
                "evidence": "I prefer local tools.",
                "links": [],
                "tags": ["preference"],
            }
        ]
    }
    registry = BackendRegistry([FakeBackend(chunks=[json.dumps(payload)])])
    vault = MemoryVault(tmp_path / "brain")
    window = MainWindow(
        registry=registry,
        library=library,
        memory_vault=vault,
        settings=QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat),
    )
    try:
        window.show()
        window._list.refresh(select_id=cid)
        app.processEvents()
        window._chat_view.remember_requested.emit()
        assert window._sidebar.current_section() == MEMORY
        assert window._memory_busy
        assert not window._chat_view.picker().isEnabled()
        wait_until(lambda: not window._memory_busy)
        assert "Created 1" in window._memory._status.text()
        assert window._memory._active.title == "Local tools"
        assert window._memory._active.source_id == cid
        window._focus_search()
        assert window._memory._search.hasFocus()
        window._memory._source.click()
        assert window._sidebar.current_section() == "chats"
        assert window._chat_view.conversation_id() == cid
        assert len(library.get_conversation(cid).messages) == 1
    finally:
        window.close()
        registry.close()
        store.close()


def test_cancelling_a_queued_memory_request_writes_nothing(tmp_path):
    qapp()
    from llm_engine.services.chat import ChatService
    from llm_engine.services.session import ModelSession
    from llm_manager_app.workers import ChatWorker

    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    cid = library.create_conversation(model=ModelRef(BackendName.OLLAMA, "fake")).summary.id
    store.add_message(cid, "user", "Remember me")
    registry = BackendRegistry([FakeBackend()])
    session = ModelSession(registry)
    worker = ChatWorker(ChatService(library, session), session)
    errors = []
    worker.memories_failed.connect(lambda code, text: errors.append(code))
    cancel = threading.Event()
    cancel.set()
    vault = MemoryVault(tmp_path / "brain")
    worker.remember(cid, vault, cancel)
    assert errors == ["cancelled"]
    assert not vault.root.exists()
    assert session.status().loaded is None
    registry.close()
    store.close()
