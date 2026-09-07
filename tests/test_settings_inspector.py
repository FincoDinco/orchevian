from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import BackendName, GenerationParams, LocalModel, ModelRef
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore

REF = ModelRef(BackendName.OLLAMA, "fake")
LOCAL = LocalModel(ref=REF, path=None, size_bytes=0)


def _qapp():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["llm-manager-tests"])
    return app


def _library(tmp_path: Path) -> tuple[SqliteStore, LibraryService]:
    store = SqliteStore(tmp_path / "data.db")
    return store, LibraryService(store)


def _settings(tmp_path: Path, name: str = "gui.ini"):
    from PySide6.QtCore import QSettings

    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def _wait_until(predicate, timeout: float = 5.0, message: str = "timeout") -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


class ProbeFake(FakeBackend):
    def __init__(self, *args: object, gate_after: int | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.params: list[GenerationParams] = []
        self.messages: list[object] = []
        self.unload_ident: int | None = None
        self.gate_after = gate_after
        self.entered = threading.Event()
        self.release = threading.Event()

    def unload(self, handle):  # type: ignore[no-untyped-def]
        self.unload_ident = threading.get_ident()
        return super().unload(handle)

    def stream_generate(self, handle, messages, params, cancel):  # type: ignore[no-untyped-def]
        del handle
        self.params.append(params)
        self.messages.append(list(messages))
        yielded = 0
        for chunk in self.chunks:
            if cancel.is_set() and not self._ignore_cancel:
                return
            yield chunk
            yielded += 1
            if self.gate_after is not None and yielded >= self.gate_after:
                self.entered.set()
                while not self.release.is_set():
                    if cancel.is_set() and not self._ignore_cancel:
                        return
                    self.release.wait(timeout=0.05)


def _window(tmp_path: Path, fake: FakeBackend | None = None, settings=None):
    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.widgets.settings import KEY_INSPECTOR_OPEN

    store, library = _library(tmp_path)
    probe = fake if fake is not None else ProbeFake(models=[LOCAL], chunks=("Hello", " world"))
    registry = BackendRegistry([probe])
    gui = settings if settings is not None else _settings(tmp_path)
    # These tests exercise visible inspector controls; the shell tests cover its closed default.
    if not gui.contains(KEY_INSPECTOR_OPEN):
        gui.setValue(KEY_INSPECTOR_OPEN, True)
    window = MainWindow(registry=registry, library=library, settings=gui)
    return window, store, library, probe


def test_studio_qss_covers_inspector_and_settings() -> None:
    from llm_manager_app.tokens import DARK, qss

    sheet = qss(DARK)
    assert len(sheet.splitlines()) < 220
    assert "inspector" in sheet
    assert "QDialog" in sheet


def test_settings_dialog_has_general_models_advanced_no_api(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QTabWidget

    from llm_manager_app.widgets.settings import SettingsDialog

    dialog = SettingsDialog(settings=_settings(tmp_path))
    try:
        tabs = dialog.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None
        labels = [tabs.tabText(i) for i in range(tabs.count())]
        assert labels == ["General", "Models", "Advanced"]
        joined = " ".join(labels).lower()
        assert "api" not in joined
        assert "server" not in joined
    finally:
        dialog.close()
        app.processEvents()


def test_settings_general_persists_in_qsettings(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QCheckBox, QComboBox

    from llm_manager_app.widgets.settings import (
        KEY_APPEARANCE,
        KEY_RETURN_SENDS,
        SettingsDialog,
        as_bool,
    )

    gui = _settings(tmp_path)
    dialog = SettingsDialog(settings=gui)
    try:
        appearance = dialog.findChild(QComboBox, "appearanceCombo")
        ret = dialog.findChild(QCheckBox, "returnSendsCheck")
        assert appearance is not None and ret is not None
        appearance.setCurrentIndex(1)
        ret.setChecked(False)
        app.processEvents()
        gui.sync()
        assert gui.value(KEY_APPEARANCE) == "light"
        assert as_bool(gui.value(KEY_RETURN_SENDS), True) is False
    finally:
        dialog.close()


def test_settings_models_writes_config_not_qsettings(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLineEdit, QPushButton

    from llm_engine.config import load
    from llm_manager_app.widgets.settings import SettingsDialog

    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "data.db"
    model_dir = tmp_path / "models"
    stored: list[dict] = []

    def config_get() -> dict:
        from llm_engine.config import get

        return get(cfg_path, db_path)

    def config_set(**fields: object):
        stored.append(dict(fields))
        from llm_engine import config as engine_config

        return engine_config.set(cfg_path, db_path, **fields)

    opened: list[Path] = []
    gui = _settings(tmp_path)
    dialog = SettingsDialog(
        settings=gui,
        config_get=config_get,
        config_set=config_set,
        db_path=db_path,
        log_path=tmp_path / "logs" / "engine.log",
        open_path=opened.append,
    )
    try:
        edit = dialog.findChild(QLineEdit, "modelDirEdit")
        save = dialog.findChild(QPushButton, "modelDirSave")
        reveal = dialog.findChild(QPushButton, "modelDirReveal")
        open_log = dialog.findChild(QPushButton, "openLogButton")
        db_edit = dialog.findChild(QLineEdit, "dbPathEdit")
        assert edit is not None and save is not None and reveal is not None
        assert open_log is not None and db_edit is not None
        assert db_edit.text() == str(db_path)
        assert db_edit.isReadOnly()
        edit.setText(str(model_dir))
        save.click()
        app.processEvents()
        assert stored == [{"model_dir": str(model_dir)}]
        loaded = load(cfg_path, db_path)
        assert loaded.model_dir == model_dir
        assert "model_dir" not in gui.allKeys()
        assert "api_port" not in gui.allKeys()
        reveal.click()
        assert opened and opened[-1] == model_dir
        open_log.click()
        assert opened[-1] == tmp_path / "logs" / "engine.log"
    finally:
        dialog.close()


def test_main_window_restores_chrome_and_last_conversation(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_engine.store.library import LibraryService
    from llm_engine.store.sqlite import SqliteStore
    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.tokens import LIGHT, qss
    from llm_manager_app.widgets.settings import (
        KEY_APPEARANCE,
        KEY_INSPECTOR_OPEN,
        KEY_LAST_CONVERSATION_ID,
        KEY_RETURN_SENDS,
        as_bool,
        as_int,
    )

    gui = _settings(tmp_path)
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    fake = ProbeFake(models=[LOCAL], chunks=("Hello", " world"))
    window = MainWindow(
        registry=BackendRegistry([fake]),
        library=library,
        settings=gui,
    )
    try:
        first = library.create_conversation().summary.id
        second = library.create_conversation().summary.id
        window._list.refresh(select_id=second)
        window._chat_view.set_inspector_open(False)
        window._chat_view.set_return_sends(False)
        gui.setValue(KEY_APPEARANCE, "light")
        window._on_appearance("light")
        app.processEvents()
        assert window._list.selected_id() == second
        assert not window._chat_view.inspector_open()
        window.close()
        app.processEvents()
        gui.sync()
        assert as_int(gui.value(KEY_LAST_CONVERSATION_ID)) == second
        assert as_bool(gui.value(KEY_INSPECTOR_OPEN), True) is False
        assert as_bool(gui.value(KEY_RETURN_SENDS), True) is False
        assert gui.value(KEY_APPEARANCE) == "light"
        assert first != second
    finally:
        if window.isVisible():
            window.close()
        store.close()

    store2 = SqliteStore(tmp_path / "data.db")
    library2 = LibraryService(store2)
    gui2 = _settings(tmp_path)
    window2 = MainWindow(
        registry=BackendRegistry([ProbeFake(models=[LOCAL])]),
        library=library2,
        settings=gui2,
    )
    try:
        window2.show()
        app.processEvents()
        assert window2._list.selected_id() == second
        assert window2._chat_view.inspector_open() is False
        assert window2._chat_view.composer().return_sends() is False
        assert qss(LIGHT) in (app.styleSheet() or "")
    finally:
        window2.close()
        store2.close()


def test_inspector_preset_applied_on_send(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    window, store, library, probe = _window(tmp_path)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        precise = window.findChild(QPushButton, "presetPrecise")
        creative = window.findChild(QPushButton, "presetCreative")
        assert precise is not None and creative is not None
        precise.click()
        assert window._chat_view.generation_params() == GenerationParams.preset("precise")
        creative.click()
        assert window._chat_view.generation_params() == GenerationParams.preset("creative")
        precise.click()
        window._chat_view.composer().set_text("hello")
        window._chat_view.composer().submit()
        _wait_until(lambda: not window._chat_view.is_streaming(), message="timed out")
        assert probe.params
        assert probe.params[0] == GenerationParams.preset("precise")
        last = window.findChild(QLabel, "lastTurnLabel")
        assert last is not None
        assert "chunks/s" in last.text()
    finally:
        window.close()
        store.close()


def test_inspector_system_prompt_writes_through(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit

    window, store, library, probe = _window(tmp_path)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        prompt = window.findChild(QPlainTextEdit, "systemPromptEdit")
        assert prompt is not None
        prompt.setPlainText("You are terse.")
        _wait_until(
            lambda: library.get_conversation(cid).system_prompt == "You are terse.",
            message="system prompt not saved",
        )
        window._chat_view.composer().set_text("hi")
        window._chat_view.composer().submit()
        _wait_until(lambda: not window._chat_view.is_streaming(), message="timed out")
        assert probe.messages
        roles = [turn.role for turn in probe.messages[0]]
        assert roles[0] == "system"
        assert probe.messages[0][0].content == "You are terse."
    finally:
        window.close()
        store.close()


def test_inspector_unload_offer_after_timeout(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    fake = ProbeFake(models=[LOCAL], chunks=tuple(["."] * 8), gate_after=1)
    window, store, library, probe = _window(tmp_path, fake=fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        inspector = window._chat_view.inspector()
        inspector.set_unload_offer_ms(20)
        unload = inspector.findChild(QPushButton, "unloadButton")
        restart = inspector.findChild(QPushButton, "restartButton")
        assert unload is not None and restart is not None
        assert unload.isHidden()
        window._chat_view.composer().set_text("hang")
        window._chat_view.composer().submit()
        assert probe.entered.wait(2.0)
        _wait_until(lambda: unload.isVisible(), timeout=2.0, message="unload offer never appeared")
        assert window._chat_view.is_streaming()
        unload.click()
        _wait_until(
            lambda: probe.unload_ident is not None or not window._chat_view.is_streaming(),
            message="unload did not run",
        )
        probe.release.set()
        _wait_until(lambda: not window._chat_view.is_streaming(), message="did not stop")
        assert unload.isHidden()
    finally:
        probe.release.set()
        window.close()
        store.close()


def test_unload_runs_on_worker_not_gui_thread(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    gui_ident = threading.get_ident()
    window, store, library, probe = _window(tmp_path)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        window._chat_view.composer().set_text("ping")
        window._chat_view.composer().submit()
        _wait_until(lambda: not window._chat_view.is_streaming(), message="timed out")
        assert window._worker is not None
        window._chat_view.unload_requested.emit()
        _wait_until(lambda: probe.unload_ident is not None, message="unload never ran")
        assert probe.unload_ident != gui_ident
        assert window._worker.thread() is not None
        assert window._worker.thread() != app.thread()
    finally:
        window.close()
        store.close()


def test_composer_ctrl_return_when_return_sends_off() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

    from llm_manager_app.widgets.composer import Composer

    host = QWidget()
    composer = Composer(host)
    layout = QVBoxLayout(host)
    layout.addWidget(composer)
    host.resize(400, 200)
    host.show()
    app.processEvents()
    composer.set_return_sends(False)
    edit = composer.findChild(QPlainTextEdit, "composerEdit")
    assert edit is not None
    sent: list[str] = []
    composer.send_requested.connect(sent.append)
    composer.set_text("hello")
    edit.setFocus()
    app.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_Return)
    assert sent == []
    assert "\n" in edit.toPlainText()
    composer.set_text("hello")
    edit.setFocus()
    app.processEvents()
    QTest.keyClick(edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert sent == ["hello"]
    host.close()


def test_shortcuts_sheet_lists_design_keys(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtGui import QAction, QKeySequence
    from PySide6.QtWidgets import QDialog, QLabel, QTabWidget

    window, store, _library, _probe = _window(tmp_path)
    try:
        window._open_shortcuts()
        app.processEvents()
        dialog = window.findChild(QDialog, "shortcutsDialog")
        assert dialog is not None
        assert "shortcut" in dialog.windowTitle().lower()
        joined = " ".join(w.text() for w in dialog.findChildren(QLabel))
        assert "New chat" in joined
        assert "Settings" in joined
        assert "Stop generation" in joined
        assert "Delete conversation" in joined
        window._open_settings()
        app.processEvents()
        settings = window.findChild(QDialog, "settingsDialog")
        assert settings is not None
        tabs = settings.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None and tabs.count() == 3
        actions = window.findChildren(QAction)
        prefs = QKeySequence(QKeySequence.StandardKey.Preferences)
        assert any(a.shortcut().matches(prefs) for a in actions)
    finally:
        window.close()
        store.close()


def test_restart_shows_error_banner(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    fake = ProbeFake(models=[LOCAL], chunks=tuple(["."] * 8), gate_after=1)
    window, store, library, probe = _window(tmp_path, fake=fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        inspector = window._chat_view.inspector()
        inspector.set_unload_offer_ms(10)
        window._chat_view.composer().set_text("hang")
        window._chat_view.composer().submit()
        assert probe.entered.wait(2.0)
        unload = inspector.findChild(QPushButton, "unloadButton")
        restart = inspector.findChild(QPushButton, "restartButton")
        _wait_until(lambda: unload is not None and unload.isVisible(), message="no unload")
        assert unload is not None and restart is not None
        unload.click()
        app.processEvents()
        restart.click()
        app.processEvents()
        assert "Unload" in window._chat_view.banner_text()
    finally:
        probe.release.set()
        window.close()
        store.close()


def test_force_unload_when_generate_ignores_cancel(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    gui_ident = threading.get_ident()
    fake = ProbeFake(
        models=[LOCAL],
        chunks=tuple(["."] * 8),
        gate_after=1,
        ignore_cancel=True,
    )
    window, store, library, probe = _window(tmp_path, fake=fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        window._chat_view.inspector().set_unload_offer_ms(10)
        window._chat_view.composer().set_text("stuck")
        window._chat_view.composer().submit()
        assert probe.entered.wait(2.0)
        unload = window._chat_view.inspector().findChild(QPushButton, "unloadButton")
        _wait_until(lambda: unload is not None and unload.isVisible(), message="no unload offer")
        assert unload is not None
        unload.click()
        _wait_until(lambda: len(probe.unload_calls) >= 1, message="backend unload never ran")
        _wait_until(lambda: not window._chat_view.is_streaming(), message="stream UI still busy")
        assert probe.unload_ident is not None and probe.unload_ident != gui_ident
        assert window._chat_view.composer().isEnabled()
    finally:
        probe.release.set()
        window.close()
        store.close()


def test_unload_during_load_keeps_gui_busy(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit, QPushButton

    gate = threading.Event()
    fake = ProbeFake(models=[LOCAL], chunks=("Hello", " world"), block_load=gate)
    window, store, library, probe = _window(tmp_path, fake=fake)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        window._chat_view.inspector().set_unload_offer_ms(10)
        window._chat_view.composer().set_text("load me")
        window._chat_view.composer().submit()
        _wait_until(lambda: bool(probe.load_calls), message="load never started")
        unload = window._chat_view.inspector().findChild(QPushButton, "unloadButton")
        _wait_until(lambda: unload is not None and unload.isVisible(), message="no unload offer")
        assert unload is not None
        unload.click()
        settled = time.monotonic() + 0.3
        while time.monotonic() < settled:
            app.processEvents()
            time.sleep(0.01)
        assert window._chat_view.is_streaming()
        composer = window._chat_view.composer()
        assert composer.isEnabled()
        edit = composer.findChild(QPlainTextEdit, "composerEdit")
        send = composer.findChild(QPushButton, "sendButton")
        assert edit is not None and not edit.isEnabled()
        assert send is not None and send.isEnabled()
        assert send.property("mode") == "stop"
        assert window._chat_service is not None
        with window._chat_service._state_lock:
            assert window._chat_service._generating is True
        assert window._session.status().loaded is None
        gate.set()
        _wait_until(
            lambda: not window._chat_view.is_streaming(),
            message="aborted load never finished",
        )
        assert window._session.status().loaded is None
        with window._chat_service._state_lock:
            assert window._chat_service._generating is False
        assert window._chat_view.banner_text()
        assert window._chat_view.composer().isEnabled()
    finally:
        gate.set()
        window.close()
        store.close()


def test_system_prompt_flush_on_send_before_debounce(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit

    window, store, library, probe = _window(tmp_path)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        prompt = window.findChild(QPlainTextEdit, "systemPromptEdit")
        assert prompt is not None
        prompt.setPlainText("You are terse.")
        assert library.get_conversation(cid).system_prompt == ""
        window._chat_view.composer().set_text("hi")
        window._chat_view.composer().submit()
        _wait_until(lambda: not window._chat_view.is_streaming(), message="timed out")
        assert library.get_conversation(cid).system_prompt == "You are terse."
        assert probe.messages
        assert probe.messages[0][0].role == "system"
        assert probe.messages[0][0].content == "You are terse."
    finally:
        window.close()
        store.close()


def test_system_prompt_flush_on_conversation_switch(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPlainTextEdit

    window, store, library, _probe = _window(tmp_path)
    try:
        first = library.create_conversation(model=REF).summary.id
        second = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=first)
        window.show()
        app.processEvents()
        prompt = window.findChild(QPlainTextEdit, "systemPromptEdit")
        assert prompt is not None
        prompt.setPlainText("Stay on rails.")
        window._list.select_id(second)
        app.processEvents()
        assert library.get_conversation(first).system_prompt == "Stay on rails."
        assert library.get_conversation(second).system_prompt == ""
    finally:
        window.close()
        store.close()


def test_settings_models_save_error_shows_label(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton

    from llm_engine.domain.errors import EngineError
    from llm_manager_app.widgets.settings import SettingsDialog

    gui = _settings(tmp_path)
    current = tmp_path / "models"

    def config_get() -> dict:
        return {"model_dir": str(current), "api_port": 8080, "db_path": str(tmp_path / "data.db")}

    def config_set(**_fields: object) -> None:
        raise EngineError("config_invalid", "disk full")

    dialog = SettingsDialog(settings=gui, config_get=config_get, config_set=config_set)
    try:
        dialog.show()
        app.processEvents()
        edit = dialog.findChild(QLineEdit, "modelDirEdit")
        save = dialog.findChild(QPushButton, "modelDirSave")
        error = dialog.findChild(QLabel, "settingsError")
        assert edit is not None and save is not None and error is not None
        edit.setText("   ")
        save.click()
        app.processEvents()
        assert not error.isHidden()
        assert "non-empty" in error.text()
        edit.setText(str(tmp_path / "other"))
        save.click()
        app.processEvents()
        assert not error.isHidden()
        assert "disk full" in error.text()
        assert "model_dir" not in gui.allKeys()
        assert "api_port" not in gui.allKeys()
    finally:
        dialog.close()


def test_missing_appearance_defaults_to_dark(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QComboBox

    from llm_manager_app.tokens import DARK, qss
    from llm_manager_app.widgets.settings import KEY_APPEARANCE, SettingsDialog

    gui = _settings(tmp_path)
    window, store, _library, _probe = _window(tmp_path, settings=gui)
    try:
        window.show()
        app.processEvents()
        assert gui.value(KEY_APPEARANCE) == "dark"
        assert qss(DARK) in (app.styleSheet() or "")
        dialog = SettingsDialog(settings=gui)
        try:
            combo = dialog.findChild(QComboBox, "appearanceCombo")
            assert combo is not None
            assert combo.currentData() == "dark"
        finally:
            dialog.close()
    finally:
        window.close()
        store.close()
