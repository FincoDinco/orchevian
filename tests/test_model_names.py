from dataclasses import replace

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import BackendName, ChatTurn, LocalModel, ModelRef
from llm_manager_app.model_names import ModelNames, friendly_name
from test_app_shell import _qapp, _window


@pytest.mark.parametrize(('raw', 'expected'), [
    ('qwen2.5-coder:14b', 'Qwen 2.5 Coder · 14B'),
    ('mlx-community/Qwen3-8B-4bit', 'Qwen 3 · 8B'),
    ('owner/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf', 'Llama 3.1 · 8B'),
    ('owner/Llama-3.1-8B-Instruct-Q8_0-00001-of-00003.gguf', 'Llama 3.1 · 8B'),
    ('owner/Qwen3-30B-A3B-Instruct-MLX-4bit', 'Qwen 3 A3B · 30B'),
    ('gemma3:4b', 'Gemma 3 · 4B'),
])
def test_friendly_names_keep_family_version_and_size(raw, expected):
    assert friendly_name(raw) == expected


def test_aliases_persist_are_isolated_by_backend_and_can_reset(tmp_path):
    _qapp()
    from PySide6.QtCore import QSettings

    ref = ModelRef(BackendName.OLLAMA, 'qwen3:8b')
    other = ModelRef(BackendName.MLX, ref.name)
    path = str(tmp_path / 'names.ini')
    names = ModelNames(QSettings(path, QSettings.Format.IniFormat))
    names.rename(ref, '  My writing helper  ')
    restored = ModelNames(QSettings(path, QSettings.Format.IniFormat))
    assert restored.display(ref) == 'My writing helper'
    assert restored.display(other) == 'Qwen 3 · 8B'
    assert ref.id == 'ollama/qwen3:8b'
    with pytest.raises(ValueError):
        restored.rename(ref, 'x' * 61)
    assert restored.display(ref) == 'My writing helper'
    restored.rename(ref, '')
    assert restored.display(ref) == 'Qwen 3 · 8B'


def test_duplicate_short_names_still_select_distinct_models(tmp_path):
    _qapp()
    from PySide6.QtCore import QSettings

    from llm_manager_app.widgets.model_picker import ModelPicker

    names = ModelNames(QSettings(str(tmp_path / 'names.ini'), QSettings.Format.IniFormat))
    refs = [ModelRef(BackendName.GGUF, f'owner/Llama-3.1-8B-Q{quant}')
            for quant in ('4_K_M', '8_0')]
    picker = ModelPicker(names=names)
    picker.set_catalog([LocalModel(ref, None, 1) for ref in refs], {'gguf': (True, None)})
    actions = [action for action in picker.menu().actions() if isinstance(action.data(), ModelRef)]
    assert len({action.text() for action in actions}) == 2
    chosen = []
    picker.model_selected.connect(chosen.append)
    for action in actions:
        action.trigger()
    assert chosen == refs


def test_custom_names_do_not_collide_with_numbered_labels(tmp_path):
    _qapp()
    from PySide6.QtCore import QSettings

    names = ModelNames(QSettings(str(tmp_path / 'names.ini'), QSettings.Format.IniFormat))
    refs = [ModelRef(BackendName.OLLAMA, f'model-{index}') for index in range(3)]
    for ref, title in zip(refs, ('Helper', 'Helper', 'Helper (1)')):
        names.rename(ref, title)
    labels = names.labels(refs)
    assert len(set(labels.values())) == len(refs)
    assert labels[refs[2].id] == 'Helper (1)'
    assert names.labels(reversed(refs)) == labels


@pytest.mark.parametrize('streaming', [True, False])
def test_renaming_updates_plain_response_without_losing_content(tmp_path, streaming):
    _qapp()
    from PySide6.QtCore import QSettings

    from llm_manager_app.widgets.chat_view import ChatView

    names = ModelNames(QSettings(str(tmp_path / 'names.ini'), QSettings.Format.IniFormat))
    ref = ModelRef(BackendName.OLLAMA, 'qwen3:8b')
    view = ChatView(names=names)
    try:
        view.picker().model_selected.emit(ref)
        transcript = view.transcript()
        transcript.set_turns([ChatTurn('user', 'Tell me a story.')])
        transcript.begin_stream()
        transcript.append_stream('Once upon a time')
        if not streaming:
            transcript.keep_stream()
        turns, buffer = transcript.turns(), transcript.buffer()
        names.rename(ref, 'Story helper')
        # Streaming renders markdown live; a kept (stopped) response stays plain.
        assert transcript.is_plain() is not streaming
        assert transcript.is_streaming() == streaming
        assert transcript.turns() == turns
        assert transcript.buffer() == buffer
        transcript.flush_stream()
        text = (transcript._browser if streaming else transcript._plain).toPlainText()
        assert 'Story helper\nOnce upon a time' in text
        assert 'Qwen 3' not in text
        if streaming:
            transcript.append_stream(', there was a fox.')
            transcript.finish_stream(parse_markdown=True)
            assert 'Story helper' in transcript._browser.toPlainText()
            assert transcript.turns()[-1].content == 'Once upon a time, there was a fox.'
    finally:
        view.close()


def test_renaming_updates_library_chat_and_picker_without_changing_saved_model(tmp_path):
    app = _qapp()
    ref = ModelRef(BackendName.OLLAMA, 'qwen3:8b')
    model = LocalModel(ref, None, 1)
    window, store, library = _window(tmp_path, BackendRegistry([FakeBackend(models=[model])]))
    try:
        conversation = library.create_conversation(model=ref)
        window._list.refresh(select_id=conversation.summary.id)
        window._on_catalog_listed([model], {'ollama': (True, None)})
        window._chat_view.set_conversation(replace(
            conversation, messages=(ChatTurn('assistant', 'Hello from your model.'),),
        ))
        window._models.select_id(ref.id)
        window._models.rename_selected('Writing helper')
        app.processEvents()
        assert window._models._model_name.text() == 'Writing helper'
        assert window._chat_view.picker().text() == 'Writing helper'
        assert 'Writing helper' in window._chat_view.transcript()._browser.toPlainText()
        assert library.get_conversation(conversation.summary.id).summary.model == ref
        window._models.apply_listed([model], {'ollama': (True, None)})
        assert window._models._model_name.text() == 'Writing helper'
        window._models._search.setText('Writing helper')
        assert window._models.selected_model().ref == ref
        window._models._search.clear()
        window._models.rename_selected('')
        assert window._chat_view.picker().text() == 'Qwen 3 · 8B'
    finally:
        window.close()
        store.close()


def test_edit_model_inline_save_cancel_and_selection_changes(tmp_path):
    _qapp()
    ref = ModelRef(BackendName.OLLAMA, 'qwen3:8b')
    other = ModelRef(BackendName.OLLAMA, 'gemma3:4b')
    models = [LocalModel(r, None, 1) for r in (ref, other)]
    window, store, _library = _window(tmp_path, BackendRegistry([FakeBackend(models=models)]))
    try:
        window._on_catalog_listed(models, {'ollama': (True, None)})
        view = window._models
        view.select_id(ref.id)
        view._rename_btn.click()
        assert not view._name_editor.isHidden()
        assert not view._name_editor.isWindow()
        view._name_edit.setText('Writing model')
        view._cancel_name_edit()
        assert window._model_names.display(ref) == 'Qwen 3 · 8B'
        view._rename_btn.click()
        view._name_edit.setText('Writing model')
        view.apply_listed(models, {'ollama': (True, None)})
        assert not view._name_editor.isHidden()
        assert view._name_edit.text() == 'Writing model'
        view._save_name.click()
        assert window._model_names.display(ref) == 'Writing model'
        view._rename_btn.click()
        view._name_edit.setText('Unsaved')
        view.select_id(other.id)
        assert view._name_editor.isHidden()
        view._save_name_edit()
        assert window._model_names.display(ref) == 'Writing model'
        assert window._model_names.display(other) == 'Gemma 3 · 4B'
        view.select_id(ref.id)
        view._rename_btn.click()
        view._name_edit.clear()
        view._save_name.click()
        assert window._model_names.display(ref) == 'Qwen 3 · 8B'
    finally:
        window.close()
        store.close()
