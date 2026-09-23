from __future__ import annotations

import threading

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.services.chat import ChatService, current_date_note
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from llm_engine.store.vault import MemoryVault
from test_app_shell import _qapp, _window
from test_settings_inspector import _wait_until

REF = ModelRef(BackendName.OLLAMA, "fake")


class PromptProbe(FakeBackend):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.prompts = []

    def stream_generate(self, handle, messages, params, cancel):
        self.prompts.append(messages)
        yield from super().stream_generate(handle, messages, params, cancel)


def test_private_chat_keeps_only_current_chat_context_and_never_writes(tmp_path, monkeypatch):
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    saved = library.create_conversation(model=REF)
    store.add_message(saved.summary.id, "user", "Saved conversation secret")
    vault = MemoryVault(tmp_path / "brain")
    vault.create("Saved memory secret")
    backend = PromptProbe()
    service = ChatService(library, ModelSession(BackendRegistry([backend])), memory_vault=vault)
    before = {
        p: p.read_bytes() for p in tmp_path.rglob("*")
        if p.is_file() and not p.name.endswith("-shm")
    }
    monkeypatch.setattr(vault, "recall", lambda *_: pytest.fail("Private chat recalled memories"))
    monkeypatch.setattr(store, "add_message", lambda *_a, **_k: pytest.fail("Private DB write"))
    private = service.create_private(REF)
    cid = private.summary.id
    try:
        service.set_system_prompt(cid, "Saved guidance must not enter the private prompt")
        service.send(cid, "Private first message")
        service._worker_thread.join(3)
        service.send(cid, "Private follow-up")
        service._worker_thread.join(3)
        # Only the date note; saved guidance stays out of the private prompt.
        assert [(turn.role, turn.content) for turn in backend.prompts[-1]] == [
            ("system", current_date_note()),
            ("user", "Private first message"), ("assistant", "Hello world"),
            ("user", "Private follow-up"),
        ]
        service.regenerate(cid)
        service._worker_thread.join(3)
        assert len(service.get_conversation(cid).messages) == 4
        assert backend.prompts[-1] == backend.prompts[-2]
        with pytest.raises(EngineError, match="cannot create memories"):
            service.capture_memories(cid, vault, threading.Event())
        service.discard_private(cid)
        with pytest.raises(EngineError, match="ended"):
            service.get_conversation(cid)
        fresh = service.create_private(REF)
        assert fresh.summary.id != cid and fresh.messages == ()
        assert [row.id for row in library.list_conversations()] == [saved.summary.id]
        assert {p: p.read_bytes() for p in before} == before
    finally:
        store.close()


def test_leaving_private_cancels_stream_and_clears_ui_and_late_callbacks(tmp_path):
    app = _qapp()
    backend = PromptProbe(block_generate=threading.Event())
    window, store, library = _window(tmp_path, registry=BackendRegistry([backend]))

    try:
        window.show()
        regular = window._list.new_chat(model=REF)
        window._chat_view.composer().set_text("Keep my regular draft")
        window._chat_view.set_inspector_open(True)
        window._private_button.click()
        private_id = window._private_id
        assert private_id < 0
        assert window._chat_view._private_banner.isVisible()
        assert "Private chat" in window.windowTitle()
        assert window._chat_view._remember.isHidden()
        window._on_model_selected(REF)
        view = window._chat_view
        view.composer().set_text("Private secret")
        view.composer().submit()
        _wait_until(lambda: bool(backend.prompts))
        assert window._settings.value("last_conversation_id") == regular
        window._chat_view.end_private_requested.emit()
        _wait_until(lambda: not window._chat_service._generating)
        app.processEvents()
        assert window._private_id is None
        assert window._chat_service._private == {}
        assert view._buffer == "" and view._pending is None
        assert "Private secret" not in " ".join(turn.content for turn in view.transcript().turns())
        assert view.composer().text() == "Keep my regular draft"
        assert view.inspector_open()
        assert library.get_conversation(regular).messages == ()
        assert window._pending_memories == set()
        view.on_rejected(private_id, "cancelled", "Private secret")
        assert private_id not in view._rejected_banners
        assert private_id not in view._rejected_drafts
        window._private_button.click()
        assert window._private_id != private_id
        assert window._chat_service.get_conversation(window._private_id).messages == ()
        window.close()
        assert window._chat_service._private == {}
    finally:
        window.close()
        store.close()


def test_private_owns_workspace_until_explicitly_cleared(tmp_path):
    app = _qapp()
    window, store, _library = _window(tmp_path)
    from llm_manager_app.widgets.settings import KEY_AUTO_MEMORY, as_bool
    from llm_manager_app.widgets.sidebar import CHATS, SETTINGS

    try:
        window.show()
        window._private_button.click()
        window._chat_view.composer().set_text("Unsent private draft")
        window._open_settings()
        window._sidebar.select_section(CHATS)
        app.processEvents()
        assert window._private_id is not None
        assert window._sidebar.isHidden()
        assert window._workspace_toolbar.isHidden()
        assert window._detail_stack.currentWidget() is window._chat_view
        assert window._workspace.width() == window._splitter.width()
        assert window._chat_view.composer().text() == "Unsent private draft"
        assert not window._new_chat_action.isEnabled()
        window._chat_view.end_private_requested.emit()
        assert window._sidebar.isVisible()
        assert window._workspace_toolbar.isVisible()
        assert window._new_chat_action.isEnabled()
        window._open_settings()
        app.processEvents()
        settings = window._settings_dialog
        assert window._sidebar.current_section() == SETTINGS
        assert window._detail_stack.currentWidget() is settings
        assert not settings.isWindow()
        assert settings.window() is window
        assert window._private_id is None
        assert window._chat_view.composer().text() == ""
        assert settings._automatic_memory.isChecked()
        settings._automatic_memory.setChecked(False)
        assert not as_bool(window._settings.value(KEY_AUTO_MEMORY), True)
        window._sidebar.select_section(CHATS)
        assert window._detail_stack.currentWidget() is window._chat_view
        window._open_settings()
        assert window._settings_dialog is settings
        assert not settings._automatic_memory.isChecked()
        settings.back_requested.emit()
        assert window._sidebar.current_section() == CHATS
    finally:
        window.close()
        store.close()


def test_window_retains_native_frame_and_can_move_and_resize(tmp_path):
    app = _qapp()
    from PySide6.QtCore import QPoint, QSize, Qt

    window, store, _library = _window(tmp_path)
    try:
        window.show()
        app.processEvents()
        for flag in ("FramelessWindowHint", "ExpandedClientAreaHint", "NoTitleBarBackgroundHint"):
            value = getattr(Qt.WindowType, flag, None)
            if value is not None:
                assert not window.windowFlags() & value
        assert window.maximumWidth() > window.minimumWidth()
        assert window.maximumHeight() > window.minimumHeight()
        window.move(80, 90)
        window.resize(1150, 730)
        app.processEvents()
        assert window.pos() == QPoint(80, 90)
        assert window.size() == QSize(1150, 730)
        window._private_button.click()
        window.resize(1200, 780)
        app.processEvents()
        assert window.size() == QSize(1200, 780)
        assert window._workspace.width() == window._splitter.width()
        window._clear_private()
        assert window.size() == QSize(1200, 780)
    finally:
        window.close()
        store.close()
