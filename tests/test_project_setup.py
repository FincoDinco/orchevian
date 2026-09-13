from dataclasses import replace

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import BackendName, LocalModel, ModelRef
from llm_manager_app.widgets.project_sheet import ProjectSheet
from test_app_shell import _qapp, _window

REF = ModelRef(BackendName.OLLAMA, 'qwen3:8b')
MODEL = LocalModel(REF, None, 1)


def test_project_model_refresh_preserves_unavailable_saved_reference():
    _qapp()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    sheet = ProjectSheet(host, draft=('Writing', 'Keep it simple.', REF))
    try:
        assert sheet.values()[2] == REF
        sheet.set_catalog([MODEL], {'ollama': (True, None)})
        sheet.set_catalog([replace(MODEL, available=False)], {'ollama': (True, None)})
        row = sheet._model.findData(REF)
        assert not sheet._model.model().index(row, 0).flags() & Qt.ItemFlag.ItemIsEnabled
        assert sheet.values() == ('Writing', 'Keep it simple.', REF)
        sheet.set_catalog([], {'ollama': (True, None)})
        assert sheet.values()[2] == REF
        assert 'missing' in sheet.choice.hint.text().lower()
        assert not sheet.isWindow()
    finally:
        sheet.close()


def test_get_more_models_preserves_embedded_creation_draft(tmp_path, monkeypatch):
    app = _qapp()
    window, store, library = _window(tmp_path)
    monkeypatch.setattr(window._models._discovery, 'request', lambda *_: None)
    window.show()
    app.processEvents()
    try:
        window._sidebar._project_btn.click()
        form = window._project_form
        assert window._detail_stack.currentWidget() is form
        assert not form.isWindow()
        form._name.setText('My writing')
        form._instructions.setPlainText('Use plain language.')
        form.browse_requested.emit()
        assert library.list_projects() == []
        assert window._models.return_to_project.isVisible()
        assert window._models._tabs.currentIndex() == 1
        window._on_catalog_listed([MODEL], {'ollama': (True, None)})
        window._models.return_to_project.click()
        assert window._detail_stack.currentWidget() is form
        assert form.values() == ('My writing', 'Use plain language.', None)
        form._model.setCurrentIndex(form._model.findData(REF))
        form.accept()
        project = library.list_projects()[0]
        assert project.default_model == REF
        assert window._detail_stack.currentWidget() is window._project_home
        assert window._project_form is None
        assert not window._models.return_to_project.isVisible()
        assert library.list_conversations() == []
        cid = window._list.new_chat()
        chat = library.get_conversation(cid)
        assert window._detail_stack.currentWidget() is window._chat_view
        assert chat.summary.project_id == project.id
        assert chat.summary.model == REF
        assert chat.system_prompt == 'Use plain language.'
    finally:
        window.close()
        store.close()


def test_cancel_project_does_not_create_project_or_keep_draft(tmp_path):
    _qapp()
    window, store, library = _window(tmp_path)
    try:
        window._sidebar.new_project()
        window._project_form._name.setText('An unfinished project')
        window._project_form.reject()
        assert library.list_projects() == []
        assert window._project_form is None
        window._sidebar.new_project()
        assert window._project_form.values() == ('', '', None)
    finally:
        window.close()
        store.close()


def test_project_home_owns_drafts_and_edits_only_future_chat_defaults(tmp_path, monkeypatch):
    _qapp()
    window, store, library = _window(tmp_path)
    monkeypatch.setattr(window._models._discovery, 'request', lambda *_: None)
    try:
        first = library.create_project('Writing', 'Original guidance', REF)
        second = library.create_project('Reading')
        original = library.create_conversation(first.id)
        window._sidebar.refresh(select_project_id=first.id)
        home = window._project_home
        home.composer.set_text('My first draft')
        home.edit_project()
        home.editor._instructions.setPlainText('Edited guidance')
        home.editor.choice.set_current(None)
        window._sidebar.select_project(second.id)
        assert home.composer.text() == ''
        home.composer.set_text('My second draft')
        window._sidebar.select_project(first.id)
        assert home.composer.text() == 'My first draft'
        assert home.editor._instructions.toPlainText() == 'Edited guidance'
        home.editor.browse_requested.emit()
        window._models.return_to_project.click()
        assert window._detail_stack.currentWidget() is home
        assert home.composer.text() == 'My first draft'
        home.editor.accept()
        assert home.editor is None
        assert library.get_conversation(original.summary.id).system_prompt == 'Original guidance'
        assert library.get_conversation(original.summary.id).summary.model == REF
        assert library.create_conversation(first.id).system_prompt == 'Edited guidance'
        window._sidebar.select_project(second.id)
        assert home.composer.text() == 'My second draft'
        assert home.chats.count() == 0
    finally:
        window.close()
        store.close()


def test_project_home_send_uses_existing_chat_pipeline(tmp_path, monkeypatch):
    app = _qapp()
    window, store, library = _window(tmp_path, BackendRegistry([FakeBackend(models=[MODEL])]))
    try:
        p = library.create_project('Writing', 'Be concise', REF)
        window._sidebar.refresh(select_project_id=p.id)
        window._on_catalog_listed([MODEL], {'ollama': (True, None)})
        home = window._project_home
        sent = []
        # Capture the existing worker boundary; backend behavior has separate tests.
        window._chat_view.send_requested.disconnect(window._queue_send)
        window._chat_view.send_requested.connect(lambda *args: sent.append(args))
        home.composer.set_text('Write a story')
        home.composer.submit()
        app.processEvents()
        assert len(sent) == 1
        cid, text, _params = sent[0]
        assert text == 'Write a story'
        chat = library.get_conversation(cid)
        assert chat.summary.project_id == p.id
        assert chat.summary.model == REF
        assert chat.system_prompt == 'Be concise'
        assert home.composer.text() == ''
        assert window._detail_stack.currentWidget() is window._chat_view
        window._sidebar.select_project(p.id)
        assert window._detail_stack.currentWidget() is home
        assert home.chats.count() == 1
        home.composer.set_text('Another draft')
        home.set_busy(True)
        home.composer.submit()
        assert len(sent) == 1
        assert home.composer.text() == 'Another draft'
    finally:
        window.close()
        store.close()


def test_project_editor_save_stays_visible_in_compact_workspace(tmp_path):
    app = _qapp()
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    window, store, library = _window(tmp_path)
    try:
        project = library.create_project('Writing', 'Be concise', REF)
        library.create_conversation(project.id)
        window._sidebar.refresh(select_project_id=project.id)
        window.show()
        home = window._project_home
        home.edit_project()
        window.resize(1024, 680)
        QTest.qWait(100)
        app.processEvents()
        button = home.editor._ok
        center = button.mapTo(home.viewport(), QPoint(button.width() // 2, button.height() // 2))
        assert home.viewport().rect().contains(center)
        assert not home.editor.isWindow()
        home.editor._name.setText('Edited name')
        home.editor.accept()
        assert home.title.text() == 'Edited name'
        assert window._detail_stack.currentWidget() is home
    finally:
        window.close()
        store.close()
