from __future__ import annotations

from llm_engine.domain.models import ChatTurn
from test_streaming_chat import REF, _qapp, _wait_until, _window
from test_web_search import result


def test_each_chat_keeps_search_setting_and_regeneration_is_explicit(tmp_path):
    _qapp()
    window, store, library, _ = _window(tmp_path)
    try:
        first = library.create_conversation(model=REF)
        second = library.create_conversation(model=REF)
        view = window._chat_view
        view.set_conversation(first)
        assert not view.composer().web_search.isChecked()
        view.composer().web_search.setChecked(True)
        view.set_conversation(second)
        assert not view.composer().web_search.isChecked()
        assert "Web off" in view._regen.text()
        view.set_conversation(first)
        assert view.composer().web_search.isChecked()
        assert "Web on" in view._regen.text()
    finally:
        window.close()
        store.close()


def test_project_search_drafts_transfer_on_send_and_do_not_leak(tmp_path):
    _qapp()
    window, store, library, _ = _window(tmp_path)
    window._worker.done.disconnect(window._schedule_automatic_memory)
    calls = []

    def retrieve(query, cancel, progress):
        calls.append(query)
        progress("Reading web pages…")
        return result()

    window._chat_service.web.retriever = retrieve
    try:
        home = window._project_home
        first = library.create_project("First")
        second = library.create_project("Second")
        home.set_project(first)
        home.composer.web_search.setChecked(True)
        home.composer.set_text("Current information")
        home.set_project(second)
        assert not home.composer.web_search.isChecked()
        home.set_project(first)
        assert home.composer.web_search.isChecked()
        window._start_project_chat(first.id, "Current information", REF)
        _wait_until(lambda: calls and not window._chat_service._generating)
        _wait_until(lambda: not window._chat_view.is_streaming())
        assert calls == ["Current information"]
        assert window._chat_view.composer().web_search.isChecked()
        assert "Public reference" in window._chat_view.web_sources.content.toPlainText()
    finally:
        window.close()
        store.close()


def test_setting_is_captured_before_queued_worker_and_regenerate_off_clears_sources(tmp_path):
    _qapp()
    window, store, library, _ = _window(tmp_path)
    window._worker.done.disconnect(window._schedule_automatic_memory)
    calls = []
    window._chat_service.web.retriever = lambda query, *args: (calls.append(query) or result())
    try:
        first = library.create_conversation(model=REF)
        second = library.create_conversation(model=REF)
        window._list.refresh(select_id=first.summary.id)
        window._show_selected_chat()
        view = window._chat_view
        view.composer().web_search.setChecked(True)
        view.composer().set_text("Search this")
        view.composer().submit()
        view.set_conversation(second)
        assert not view.composer().web_search.isChecked()
        _wait_until(lambda: calls and not window._chat_service._generating)
        _wait_until(lambda: not view.is_streaming())
        assert calls == ["Search this"]
        view.set_conversation(library.get_conversation(first.summary.id))
        assert "Public reference" in view.web_sources.content.toPlainText()
        view.composer().web_search.setChecked(False)
        view.regenerate()
        _wait_until(lambda: not view.is_streaming())
        assert calls == ["Search this"]
        assert view.web_sources.isHidden()
    finally:
        window.close()
        store.close()


def test_private_clear_removes_sources_toggle_and_progress(tmp_path):
    _qapp()
    window, store, library, _ = _window(tmp_path)
    try:
        chat = window._chat_service
        chat.web.retriever = lambda *args: result()
        private = chat.create_private(REF)
        cid = private.summary.id
        chat._private_message(cid, ChatTurn("user", "Private question"))
        import threading
        chat.web.context(cid, "Private question", True, threading.Event(), lambda _: None)
        view = window._chat_view
        view.set_conversation(chat.get_conversation(cid))
        view.composer().web_search.setChecked(True)
        assert "Public reference" in view.web_sources.content.toPlainText()
        chat.discard_private(cid)
        view.clear_private(cid)
        assert view.web_sources.content.toPlainText() == ""
        view.set_conversation(chat.create_private(REF))
        assert not view.composer().web_search.isChecked()
        assert view.web_sources.isHidden()
    finally:
        window.close()
        store.close()
