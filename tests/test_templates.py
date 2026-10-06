from __future__ import annotations

import pytest

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from test_settings_inspector import _qapp, _window


def test_templates_persist_edit_seed_and_delete_without_changing_chats(tmp_path):
    path = tmp_path / "data.db"
    with SqliteStore(path) as store:
        library = LibraryService(store, default_model=ModelRef(BackendName.OLLAMA, "fake"))
        first = library.save_template(
            name="Review",
            description="Review drafts",
            system_prompt="Be concise.",
            user_prompt="Review this:",
        )
    with SqliteStore(path) as store:
        library = LibraryService(store)
        assert library.get_template(first.id) == first
        project = library.create_project(
            "Work", instructions="Use British spelling.", model=ModelRef(BackendName.OLLAMA, "fake")
        )
        chat = library.create_conversation(project_id=project.id, template_id=first.id)
        assert chat.system_prompt == "Use British spelling.\n\nBe concise."
        assert chat.summary.model == project.default_model
        assert chat.messages == ()
        updated = library.save_template(
            id=first.id, name="Writing review", system_prompt="Be kind."
        )
        assert updated.created_at == first.created_at
        assert len(library.list_templates()) == 1
        assert library.get_conversation(chat.summary.id).system_prompt == chat.system_prompt
        library.delete_template(first.id)
        assert library.list_templates() == []
        assert library.get_conversation(chat.summary.id) == chat
        with pytest.raises(EngineError):
            library.save_template(name="  ")
        with pytest.raises(EngineError):
            library.create_conversation(template_id=999)
        assert len(library.list_conversations()) == 1


def test_template_editor_create_search_edit_use_and_preserve_chat_drafts(tmp_path):
    app = _qapp()
    window, store, library, _fake = _window(tmp_path)
    try:
        from llm_manager_app.widgets.sidebar import TEMPLATES

        cid = library.create_conversation(model=ModelRef(BackendName.OLLAMA, "fake")).summary.id
        window._list.refresh(select_id=cid)
        window._chat_view.composer().set_text("An unfinished message")
        window._sidebar.select_section(TEMPLATES)
        editor = window._templates
        assert window._detail_stack.currentWidget() is editor
        editor.new_template()
        editor._name.setText("Review a draft")
        editor._description.setText("Writing feedback")
        editor._guidance.setPlainText("Point out unclear wording.")
        editor._prompt.setPlainText("Review the following text:")
        assert editor.save()
        template_id = editor._id
        editor._search.setText("WRITING")
        assert not editor._list.item(0).isHidden()
        editor._search.setText("no match")
        assert editor._list.item(0).isHidden()
        editor._search.clear()
        editor.use_selected()
        app.processEvents()
        new_id = window._list.selected_id()
        assert new_id != cid
        assert window._chat_view.composer().text() == "Review the following text:"
        assert library.get_conversation(new_id).system_prompt == "Point out unclear wording."
        assert library.get_conversation(new_id).messages == ()
        window._list.select_id(cid)
        assert window._chat_view.composer().text() == "An unfinished message"
        window._list.select_id(new_id)
        assert window._chat_view.composer().text() == "Review the following text:"
        editor.delete_selected(confirmed=True)
        assert not library.list_templates()
        assert library.get_conversation(new_id).system_prompt == "Point out unclear wording."
        with pytest.raises(EngineError):
            library.get_template(template_id)
    finally:
        window.close()
        store.close()


def test_unsaved_template_cancel_and_save_on_selection(tmp_path, monkeypatch):
    _qapp()
    from PySide6.QtWidgets import QMessageBox

    from llm_manager_app.widgets.templates_view import TemplatesView

    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        first = library.save_template(name="Alpha")
        second = library.save_template(name="Beta")
        editor = TemplatesView(library=library)
        editor._list.setCurrentRow(0)
        editor._guidance.setPlainText("Changed")
        monkeypatch.setattr(
            QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel
        )
        editor._list.setCurrentRow(1)
        assert editor._id == first.id
        assert editor._dirty
        assert not editor.prepare_close()
        monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Save)
        editor._list.setCurrentRow(1)
        assert editor._id == second.id
        assert editor._list.currentItem().text() == "Beta"
        assert library.get_template(first.id).system_prompt == "Changed"
        editor.close()


def test_template_editor_is_ready_to_type_without_new_template(tmp_path):
    _qapp()
    window, store, library, _fake = _window(tmp_path)
    try:
        editor = window._templates
        assert editor._editor.isEnabled()
        editor._name.setText("First try")  # No "New template" click needed.
        editor._prompt.setPlainText("Summarize this:")
        assert editor.save()
        assert [t.name for t in library.list_templates()] == ["First try"]
        editor.delete_selected(confirmed=True)
        assert editor._editor.isEnabled() and editor._name.text() == ""
    finally:
        window.close()
        store.close()
