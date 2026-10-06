from __future__ import annotations

import threading

from llm_engine.domain.errors import EngineError
from llm_engine.services.openai_api import ApiServerService
from llm_manager_app.widgets.settings import SettingsDialog
from test_openai_api import free_port
from test_settings_inspector import _qapp, _settings, _wait_until, _window


def test_settings_start_stop_port_persistence_and_errors(tmp_path):
    app = _qapp()
    gui_ident = threading.get_ident()
    calls = []
    cfg = {"api_port": 8123, "model_dir": str(tmp_path / "models")}

    class Api:
        running = False
        fail = False
        api_key = "ov-original"

        def status(self):
            return {"running": self.running, "port": cfg["api_port"], "recent_requests": []}

        def start(self, port):
            calls.append(("start", port, threading.get_ident()))
            if self.fail:
                raise EngineError("bind_failed", "Port is already in use")
            self.running = True

        def stop(self):
            calls.append(("stop", threading.get_ident()))
            self.running = False

    api = Api()
    settings = _settings(tmp_path)
    dialog = SettingsDialog(settings=settings, api=api, config_get=lambda: cfg,
                            config_set=lambda **fields: cfg.update(fields))
    try:
        dialog.show()
        app.processEvents()
        assert not dialog._api_enabled.isChecked()
        dialog._api_port.setValue(8765)
        dialog._api_enabled.click()
        _wait_until(lambda: dialog._api_task is None)
        assert cfg["api_port"] == 8765
        assert dialog._api_enabled.isChecked()
        assert not dialog._api_port.isEnabled()
        assert "8765/v1" in dialog._api_url.text()
        assert "Running" in dialog._api_status.text()
        assert calls[0][2] != gui_ident
        assert not settings.contains("api_port")
        dialog._api_enabled.click()
        _wait_until(lambda: dialog._api_task is None)
        assert dialog._api_port.isEnabled()
        assert "Stopped" in dialog._api_status.text()
        api.fail = True
        dialog._api_enabled.click()
        _wait_until(lambda: dialog._api_task is None)
        assert not dialog._api_enabled.isChecked()
        assert "Port is already in use" in dialog._api_status.text()
    finally:
        dialog.wait_for_api_change()
        dialog.close()


def test_main_window_owns_shared_api_and_closes_listener(tmp_path):
    _qapp()
    window, store, library, _fake = _window(tmp_path)
    try:
        assert isinstance(window._api, ApiServerService)
        assert window._api._chat is window._chat_service
        port = free_port()
        window._api.start(port)
        window.close()
        assert not window._api.status()["running"]
        import httpx
        import pytest
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1)
    finally:
        window.close()
        store.close()


def test_api_key_is_shown_copied_and_regenerated(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

    from llm_manager_app.secret_store import API_KEY_SECRET, SecretStore
    from llm_manager_app.widgets.settings import SettingsDialog

    _qapp()

    class Api:
        api_key = "ov-original"

        def status(self):
            return {"running": False, "port": 8123, "recent_requests": []}

    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    api = Api()
    dialog = SettingsDialog(settings=settings, config_get=lambda: {"api_port": 8123},
                            config_set=lambda **_: None, api=api)
    try:
        field = dialog.findChild(QLineEdit, "apiKey")
        assert field.text() == "ov-original"
        assert field.echoMode() == QLineEdit.EchoMode.Password
        dialog.findChild(QPushButton, "copyApiKey").click()
        assert QApplication.clipboard().text() == "ov-original"
        dialog.findChild(QPushButton, "regenerateApiKey").click()
        assert api.api_key.startswith("ov-") and api.api_key != "ov-original"
        assert SecretStore(settings, vault=None).get(API_KEY_SECRET) == api.api_key
        assert field.text() == api.api_key
    finally:
        dialog.close()


def test_unsaved_api_key_is_explained_and_kept_when_regenerating_fails(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

    from llm_manager_app import secret_store
    from test_secret_store import LockedVault

    _qapp()

    class Api:
        api_key = "ov-session-only"

        def status(self):
            return {"running": False, "port": 8123, "recent_requests": []}

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(secret_store, "_vault", LockedVault)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    api = Api()
    dialog = SettingsDialog(settings=settings, config_get=lambda: {"api_port": 8123},
                            config_set=lambda **_: None, api=api)
    try:
        note = dialog.findChild(QLabel, "apiKeyNote")
        assert "will change when Orchevian restarts" in note.text()
        dialog.findChild(QPushButton, "regenerateApiKey").click()
        assert api.api_key == "ov-session-only"
        assert warnings and "Couldn't save the key" in warnings[0]
    finally:
        dialog.close()
