from __future__ import annotations

import json
import threading

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import BackendName, ModelRef
from llm_manager_app import main_window
from llm_manager_app.widgets.settings import KEY_AUTO_MEMORY
from llm_manager_app.widgets.sidebar import CHATS, PRIVATE, SETTINGS
from test_app_shell import _qapp, _window
from test_settings_inspector import _wait_until

REF = ModelRef(BackendName.OLLAMA, "fake")


@pytest.fixture(autouse=True)
def remember_without_waiting(monkeypatch):
    # These tests check what is remembered; the quiet-period tests set their own wait.
    monkeypatch.setattr(main_window, "AUTO_MEMORY_QUIET_SECONDS", 0)


class MemoryBackend(FakeBackend):
    def __init__(self, *, block_capture=False):
        super().__init__()
        self.captures = 0
        self.block_capture = block_capture
        self.entered = threading.Event()

    def stream_generate(self, handle, messages, params, cancel):
        if messages[0].role != "system" or "knowledge base" not in messages[0].content:
            yield "I will keep that in mind."
            return
        self.captures += 1
        self.entered.set()
        if self.block_capture:
            while not cancel.is_set():
                cancel.wait(0.01)
            return
        yield json.dumps({"notes": [{
            "title": "Local tools", "body": "The user prefers local tools.",
            "evidence": "I prefer local tools.", "tags": ["preference"], "links": [],
        }]})


def send(window, text="I prefer local tools."):
    window._chat_view.composer().set_text(text)
    window._chat_view.composer().submit()
    _wait_until(lambda: not window._chat_view.is_streaming())


def test_completed_regular_chats_automatically_add_filtered_memories_without_navigation(tmp_path):
    _qapp()
    backend = MemoryBackend()
    window, store, library = _window(tmp_path, registry=BackendRegistry([backend]))
    try:
        window.show()
        cid = window._list.new_chat(model=REF)
        send(window)
        _wait_until(lambda: backend.captures == 1 and not window._memory_busy)
        assert window._sidebar.current_section() == CHATS
        assert window._detail_stack.currentWidget() is window._chat_view
        notes = window._memory_vault.list_notes()
        memories = [note for note in notes if note.kind == "memory"]
        assert len(memories) == 1
        assert memories[0].title == "Local tools"
        assert memories[0].source_id == cid
        assert "I prefer local tools." in memories[0].body
        assert len(library.get_conversation(cid).messages) == 2
        send(window, "Yes, I still prefer local tools.")
        _wait_until(lambda: backend.captures == 2 and not window._memory_busy)
        assert len([n for n in window._memory_vault.list_notes() if n.kind == "memory"]) == 1
    finally:
        window.close()
        store.close()


def test_disabled_cancelled_and_private_chats_do_not_schedule_memories(tmp_path):
    _qapp()
    backend = MemoryBackend()
    window, store, _library = _window(tmp_path, registry=BackendRegistry([backend]))
    try:
        window.show()
        window._settings.setValue(KEY_AUTO_MEMORY, False)
        cid = window._list.new_chat(model=REF)
        send(window)
        assert not window._pending_memories
        window._settings.setValue(KEY_AUTO_MEMORY, True)
        window._schedule_automatic_memory(cid, True, 5, 1.0, 1.0)
        window._schedule_automatic_memory(cid, False, 0, 1.0, 1.0)
        assert not window._pending_memories
        window._sidebar.select_section(PRIVATE)
        window._on_model_selected(REF)
        send(window)
        assert not window._pending_memories
        assert backend.captures == 0
        assert window._memory_vault.list_notes() == []
    finally:
        window.close()
        store.close()


def test_turning_off_automatic_memory_cancels_active_capture(tmp_path):
    _qapp()
    backend = MemoryBackend(block_capture=True)
    window, store, _library = _window(tmp_path, registry=BackendRegistry([backend]))
    try:
        window.show()
        window._list.new_chat(model=REF)
        send(window)
        _wait_until(backend.entered.is_set)
        window._open_settings()
        assert window._sidebar.current_section() == SETTINGS
        window._settings_dialog._automatic_memory.setChecked(False)
        _wait_until(lambda: not window._memory_busy)
        assert not window._pending_memories
        assert window._memory_vault.list_notes() == []
        assert window._detail_stack.currentWidget() is window._settings_dialog
    finally:
        window.close()
        store.close()


def test_automatic_memory_waits_for_a_quiet_chat_or_a_move_to_another(tmp_path, monkeypatch):
    _qapp()
    monkeypatch.setattr(main_window, "AUTO_MEMORY_QUIET_SECONDS", 3600)
    backend = MemoryBackend()
    window, store, _library = _window(tmp_path, registry=BackendRegistry([backend]))
    try:
        window.show()
        first = window._list.new_chat(model=REF)
        send(window)
        window._drain_automatic_memories()
        assert not window._memory_busy and first in window._pending_memories
        window._list.new_chat(model=REF)  # Moving on lets the first chat be remembered.
        window._drain_automatic_memories()
        _wait_until(lambda: backend.captures == 1 and not window._memory_busy)
        assert first not in window._pending_memories
    finally:
        window.close()
        store.close()


def test_a_message_sent_while_remembering_goes_first(tmp_path, monkeypatch):
    _qapp()
    backend = MemoryBackend(block_capture=True)
    window, store, library = _window(tmp_path, registry=BackendRegistry([backend]))
    try:
        window.show()
        cid = window._list.new_chat(model=REF)
        send(window)
        _wait_until(backend.entered.is_set)
        monkeypatch.setattr(main_window, "AUTO_MEMORY_QUIET_SECONDS", 3600)
        send(window, "Next question")
        _wait_until(lambda: not window._memory_busy)
        assert len(library.get_conversation(cid).messages) == 4
        assert cid in window._pending_memories  # Tried again once the chat is quiet.
        assert "Paused so your message could go first" in window._memory._status.text()
    finally:
        window.close()
        store.close()


def test_settings_can_turn_off_using_memories_in_step_with_the_memoria_page(tmp_path):
    _qapp()
    window, store, _library = _window(tmp_path, registry=BackendRegistry([MemoryBackend()]))
    try:
        window.show()
        window._open_settings()
        window._settings_dialog._memory_recall.setChecked(False)
        assert not window._memory._recall.isChecked()
        assert window._settings.value("memory/recall") in (False, "false")
    finally:
        window.close()
        store.close()
