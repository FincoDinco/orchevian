import pytest

from llm_engine.domain.models import BackendName, LocalModel, ModelRef
from llm_manager_app.model_preferences import default_model, save_default_model
from test_app_shell import _library, _qapp, _window

APP = ModelRef(BackendName.OLLAMA, 'qwen3:8b')
PROJECT = ModelRef(BackendName.GGUF, 'owner/qwen3:8b')
EXPLICIT = ModelRef(BackendName.MLX, 'owner/qwen3:8b')


@pytest.mark.parametrize(('project_model', 'explicit', 'expected'), [
    (None, None, APP), (PROJECT, None, PROJECT),
    (PROJECT, EXPLICIT, EXPLICIT), (None, EXPLICIT, EXPLICIT),
])
def test_model_precedence_and_existing_chat_independence(
    tmp_path, project_model, explicit, expected,
):
    store, library = _library(tmp_path)
    try:
        old = library.create_conversation()
        library.default_model = APP
        p = library.create_project('Project', 'Guidance', project_model)
        chat = library.create_conversation(p.id, explicit)
        assert chat.summary.model == expected
        assert chat.system_prompt == 'Guidance'
        assert library.create_conversation().summary.model == APP
        library.default_model = EXPLICIT
        library.update_project(p.id, model=EXPLICIT)
        assert library.get_conversation(chat.summary.id).summary.model == expected
        assert library.get_conversation(old.summary.id).summary.model is None
    finally:
        store.close()


def test_default_preference_roundtrips_backend_and_resets(tmp_path):
    from PySide6.QtCore import QSettings

    path = str(tmp_path / 'prefs.ini')
    for backend in BackendName:
        settings = QSettings(path, QSettings.Format.IniFormat)
        ref = ModelRef(backend, 'owner/model:tag')
        save_default_model(settings, ref)
        assert default_model(QSettings(path, QSettings.Format.IniFormat)) == ref
    save_default_model(settings, None)
    assert default_model(settings) is None


def test_settings_default_keeps_missing_or_unavailable_model(tmp_path):
    _qapp()
    from PySide6.QtCore import Qt

    window, store, library = _window(tmp_path)
    try:
        window._open_settings()
        panel = window._settings_dialog
        panel.set_catalog([LocalModel(APP, None, 1)], {'ollama': (True, None)})
        picker = panel._default_model
        picker.combo.setCurrentIndex(picker.combo.findData(APP))
        assert default_model(window._settings) == APP
        panel.reload()  # Deserializes a different Python object for the same reference.
        assert picker.combo.currentText() == 'Qwen 3 · 8B'
        assert picker.combo.count() == 3  # Empty choice, backend header, model.
        assert library.create_conversation().summary.model == APP
        panel.set_catalog([], {'ollama': (False, 'Ollama is not running')})
        assert picker.current() == APP
        assert 'Ollama is not running' in picker.hint.text()
        assert default_model(window._settings) == APP
        panel.set_catalog([], {'ollama': (True, None)})
        assert 'missing' in picker.hint.text().lower()
        assert not picker.combo.model().index(picker.combo.currentIndex(), 0).flags() & (
            Qt.ItemFlag.ItemIsEnabled
        )
        assert library.create_conversation().summary.model == APP
        picker.combo.setCurrentIndex(0)
        assert library.create_conversation().summary.model is None
        assert default_model(window._settings) is None
    finally:
        window.close()
        store.close()


def test_startup_applies_saved_default_and_project_home_inherits_it(tmp_path):
    _qapp()
    from PySide6.QtCore import QSettings

    settings = QSettings(str(tmp_path / 'gui.ini'), QSettings.Format.IniFormat)
    save_default_model(settings, APP)
    window, store, library = _window(tmp_path)
    try:
        assert library.create_conversation().summary.model == APP
        project = library.create_project('Writing')
        window._sidebar.refresh(select_project_id=project.id)
        home = window._project_home
        assert home.effective_model() == APP
        window._on_catalog_listed([], {'ollama': (False, 'Ollama is not running')})
        home.composer.set_text('My draft')
        assert not home.composer._send.isEnabled()
        assert 'Ollama is not running' in home.status.text()
        assert home.composer.text() == 'My draft'
        window._default_model_changed(EXPLICIT)
        assert home.effective_model() == EXPLICIT
        library.update_project(project.id, model=PROJECT)
        window._sidebar.select_project(project.id)
        assert home.effective_model() == PROJECT
        assert home.composer.text() == 'My draft'
    finally:
        window.close()
        store.close()
