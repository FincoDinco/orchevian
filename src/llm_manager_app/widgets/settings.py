"""Settings workspace (Ctrl/Cmd+,) and keyboard shortcuts sheet."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from llm_engine import config
from llm_engine.services.openai_api import new_api_key
from llm_manager_app.model_preferences import default_model, save_default_model
from llm_manager_app.secret_store import API_KEY_SECRET, SecretStore
from llm_manager_app.widgets.web_search_settings import WebSearchSettings

ORG_NAME = "Orchevian"
APP_NAME = "Orchevian"

KEY_AUTO_MEMORY = "memory/automatic"
KEY_APPEARANCE = "appearance"
KEY_RETURN_SENDS = "return_sends"
KEY_INSPECTOR_OPEN = "inspector_open"
KEY_LAST_CONVERSATION_ID = "last_conversation_id"
DEFAULT_APPEARANCE = "dark"

_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+N", "New chat"),
    ("Ctrl+Shift+N", "New project"),
    ("Ctrl+Shift+P", "Private chat"),
    ("Ctrl+,", "Settings"),
    ("Ctrl+1", "Chats"),
    ("Ctrl+2", "Models"),
    ("Ctrl+3", "Second Brain"),
    ("Ctrl+4", "Downloads"),
    ("Ctrl+5", "Templates"),
    ("Ctrl+Meta+S" if sys.platform == "darwin" else "Ctrl+Shift+S", "Show or hide sidebar"),
    ("Ctrl+Shift+.", "Force stop model"),
    ("Ctrl+L", "Focus composer"),
    ("Ctrl+F", "Focus list search"),
    ("Return", "Send (Shift+Return = newline); Settings can flip to Ctrl/⌘+Return"),
    ("Escape", "Stop generation, loading, or memory capture"),
    ("Ctrl+Backspace", "Delete conversation"),
    ("Delete", "Delete conversation"),
)


def as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def migrate_settings(settings: QSettings, legacy: QSettings) -> QSettings:
    """Import preferences once, preserving newer values and later user deletions."""
    marker = "migration/llm_manager"
    if as_bool(settings.value(marker, False), False):
        return settings
    for key in legacy.allKeys():
        if not settings.contains(key):
            settings.setValue(key, legacy.value(key))
    settings.setValue(marker, True)
    settings.sync()
    return settings


def make_settings() -> QSettings:
    return migrate_settings(
        QSettings(ORG_NAME, APP_NAME), QSettings("llm-manager", "LLM Manager"),
    )


def appearance_theme(settings: QSettings) -> str:
    theme = str(settings.value(KEY_APPEARANCE, DEFAULT_APPEARANCE) or DEFAULT_APPEARANCE)
    theme = theme.strip().lower()
    return theme if theme in {"light", "dark"} else DEFAULT_APPEARANCE


def ensure_appearance(settings: QSettings) -> str:
    theme = appearance_theme(settings)
    stored = str(settings.value(KEY_APPEARANCE, "") or "").strip().lower()
    if stored not in {"light", "dark"}:
        settings.setValue(KEY_APPEARANCE, theme)
    return theme


def _native(seq: str) -> str:
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)


def _open_path(path: Path) -> None:
    target = path if path.exists() else path.parent
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


class SettingsDialog(QWidget):
    api_finished = Signal(str)
    default_model_changed = Signal(object)
    back_requested = Signal()
    automatic_memory_changed = Signal(bool)
    appearance_changed = Signal(str)
    return_sends_changed = Signal(bool)
    rescan_requested = Signal()
    web_search_keys_changed = Signal(list)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: QSettings,
        config_get: Callable[..., dict[str, Any]] | None = None,
        config_set: Callable[..., Any] | None = None,
        db_path: Path | str | None = None,
        log_path: Path | str | None = None,
        open_path: Callable[[Path], None] | None = None,
        names=None,
        api=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.resize(560, 400)

        self._settings = settings
        self._api = api
        self._api_task: threading.Thread | None = None
        self.api_finished.connect(self._api_complete)
        self._names = names
        self._config_get = config_get or config.get
        self._config_set = config_set or config.set
        self._db_path = Path(db_path) if db_path is not None else None
        self._log_path = Path(log_path) if log_path is not None else config.default_log_path()
        self._open_path = open_path or _open_path

        tabs = QTabWidget(self)
        tabs.setObjectName("settingsTabs")
        tabs.addTab(self._build_general(), "General")
        self.web_search = WebSearchSettings(settings, self)
        self.web_search.keys_changed.connect(self.web_search_keys_changed)
        tabs.addTab(self.web_search, "Web Search")
        tabs.addTab(self._build_models(), "Models")
        if self._api is not None:
            tabs.addTab(self._build_api(), "API")
        tabs.addTab(self._build_advanced(), "Advanced")

        heading = QLabel("Settings", self)
        heading.setObjectName("pageTitle")
        back = QPushButton("Back to chats", self)
        back.clicked.connect(self.back_requested)
        header = QHBoxLayout()
        header.addWidget(heading)
        header.addStretch()
        header.addWidget(back)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.addLayout(header)
        layout.setSpacing(16)
        layout.addWidget(tabs, 1)
        self.reload()

    def reload(self) -> None:
        self._default_model.set_current(default_model(self._settings))
        appearance = appearance_theme(self._settings)
        blocked = self._appearance.blockSignals(True)
        self._appearance.setCurrentIndex(0 if appearance == "dark" else 1)
        self._appearance.blockSignals(blocked)

        return_sends = as_bool(self._settings.value(KEY_RETURN_SENDS, True), True)
        blocked = self._return_sends.blockSignals(True)
        self._return_sends.setChecked(return_sends)
        self._return_sends.blockSignals(blocked)

        blocked = self._automatic_memory.blockSignals(True)
        self._automatic_memory.setChecked(
            as_bool(self._settings.value(KEY_AUTO_MEMORY, True), True)
        )
        self._automatic_memory.blockSignals(blocked)
        self._reload_engine_paths()
        self._models_error.hide()
        if self._api is not None:
            if not self._api.status()["running"] and self._api_task is None:
                self._api_port.setValue(int(self._config_get().get("api_port", 8080)))
            self._refresh_api()

    def _build_api(self) -> QWidget:
        page = QWidget(self)
        self._api_enabled = QCheckBox("Enable local API", page)
        self._api_enabled.setObjectName("apiEnabled")
        self._api_enabled.toggled.connect(self._toggle_api)
        self._api_port = QSpinBox(page)
        self._api_port.setObjectName("apiPort")
        self._api_port.setRange(1, 65535)
        self._api_port.setValue(int(self._config_get().get("api_port", 8080)))
        self._api_port.valueChanged.connect(self._refresh_api)
        self._api_url = QLineEdit(page)
        self._api_url.setObjectName("apiUrl")
        self._api_url.setReadOnly(True)
        copy = QPushButton("Copy URL", page)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._api_url.text()))
        self._api_key = QLineEdit(page)
        self._api_key.setObjectName("apiKey")
        self._api_key.setReadOnly(True)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setText(self._api.api_key)
        copy_key = QPushButton("Copy Key", page)
        copy_key.setObjectName("copyApiKey")
        copy_key.clicked.connect(lambda: QApplication.clipboard().setText(self._api.api_key))
        regenerate = QPushButton("Regenerate", page)
        regenerate.setObjectName("regenerateApiKey")
        regenerate.setToolTip("Make a new key. Clients using the old key stop working.")
        regenerate.clicked.connect(self._regenerate_api_key)
        self._api_status = QLabel("Stopped", page)
        self._api_status.setObjectName("apiStatus")
        self._api_status.setWordWrap(True)
        hint = QLabel(
            "Connect local tools using an OpenAI-compatible API. It shares the model with "
            "chat and Second Brain, so only one request can run at a time. "
            "API messages are not saved and do not use your notes. "
            "The API is available only on this computer and starts disabled each launch. "
            "Clients must send the API key (most apps have an API key field for it).", page,
        )
        hint.setWordWrap(True)
        self._api_example = QPlainTextEdit(page)
        self._api_example.setObjectName("apiExample")
        self._api_example.setReadOnly(True)
        self._api_example.setMaximumHeight(115)
        self._api_recent = QPlainTextEdit(page)
        self._api_recent.setObjectName("apiRecentRequests")
        self._api_recent.setReadOnly(True)
        self._api_recent.setMaximumBlockCount(50)
        layout = QVBoxLayout(page)
        layout.addWidget(hint)
        form = QFormLayout()
        form.addRow(self._api_enabled)
        form.addRow("Port", self._api_port)
        row = QHBoxLayout()
        row.addWidget(self._api_url)
        row.addWidget(copy)
        form.addRow("Base URL", row)
        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key)
        key_row.addWidget(copy_key)
        key_row.addWidget(regenerate)
        form.addRow("API key", key_row)
        form.addRow("Status", self._api_status)
        layout.addLayout(form)
        layout.addWidget(QLabel("Try it in a terminal", page))
        layout.addWidget(self._api_example)
        layout.addWidget(QLabel("Recent requests (kept in memory; no message content)", page))
        layout.addWidget(self._api_recent, 1)
        self._api_timer = QTimer(self)
        self._api_timer.setInterval(1000)
        self._api_timer.timeout.connect(self._refresh_api)
        self._api_timer.start()
        return page

    def _refresh_api(self, *_args) -> None:
        if self._api is None:
            return
        status = self._api.status()
        running = status["running"]
        changing = self._api_task is not None
        self._api_enabled.setEnabled(not changing)
        self._api_port.setEnabled(not running and not changing)
        if not changing:
            blocked = self._api_enabled.blockSignals(True)
            self._api_enabled.setChecked(running)
            self._api_enabled.blockSignals(blocked)
        port = status["port"] if running else self._api_port.value()
        url = f"http://127.0.0.1:{port}/v1"
        self._api_url.setText(url)
        auth = "-H 'Authorization: Bearer YOUR_API_KEY'"
        example = (f"curl {url}/models {auth}\n\n"
                   f"curl {url}/chat/completions {auth} -H 'Content-Type: application/json' "
                   "-d '{\"model\":\"ollama/MODEL_NAME\",\"messages\":["
                   "{\"role\":\"user\",\"content\":\"Hello\"}],\"stream\":true}'")
        if self._api_example.toPlainText() != example:
            self._api_example.setPlainText(example)
        recent = "\n".join(
            f"{r['status']}  {r['method']} {r['path']}  ({r['elapsed_ms']} ms to headers)"
            for r in reversed(status["recent_requests"])
        )
        if self._api_recent.toPlainText() != recent:
            self._api_recent.setPlainText(recent)

    def _regenerate_api_key(self) -> None:
        key = new_api_key()
        SecretStore(self._settings).set(API_KEY_SECRET, key)
        self._api.api_key = key
        self._api_key.setText(key)

    def _toggle_api(self, enabled: bool) -> None:
        if self._api_task is not None:
            return
        port = self._api_port.value()
        self._api_status.setText("Starting…" if enabled else "Stopping…")

        def change() -> None:
            error = ""
            try:
                if enabled:
                    self._config_set(api_port=port)
                    self._api.start(port=port)
                else:
                    self._api.stop()
            except Exception as exc:
                error = str(exc)
            self.api_finished.emit(error)

        self._api_task = threading.Thread(target=change, daemon=True, name="api-settings")
        self._api_task.start()
        self._refresh_api()

    def _api_complete(self, error: str) -> None:
        self._api_task = None
        status = "Running" if self._api.status()["running"] else "Stopped"
        self._api_status.setText(error or status)
        self._refresh_api()

    def wait_for_api_change(self) -> None:
        if self._api_task is not None:
            self._api_task.join()

    def _build_general(self) -> QWidget:
        page = QWidget(self)
        self._appearance = QComboBox(page)
        self._appearance.setObjectName("appearanceCombo")
        self._appearance.addItem("Dark", "dark")
        self._appearance.addItem("Light", "light")
        self._appearance.currentIndexChanged.connect(self._on_appearance)

        self._return_sends = QCheckBox("Return sends", page)
        self._return_sends.setObjectName("returnSendsCheck")
        self._return_sends.setToolTip("When off, Ctrl/⌘+Return sends and Return inserts a newline.")
        self._return_sends.toggled.connect(self._on_return_sends)

        hint = QLabel("When off, Ctrl/⌘+Return sends and Return inserts a newline.", page)
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)

        form = QFormLayout(page)
        form.setContentsMargins(8, 12, 8, 8)
        form.setSpacing(8)
        form.addRow("Appearance", self._appearance)
        form.addRow("Composer", self._return_sends)
        form.addRow("", hint)
        self._automatic_memory = QCheckBox("Automatically remember chats", page)
        self._automatic_memory.setObjectName("automaticMemoryCheck")
        self._automatic_memory.toggled.connect(self._on_automatic_memory)
        memory_hint = QLabel(
            "After regular chats, your local AI selects useful facts, preferences, and decisions "
            "and connects them in Second Brain. Repeated or unhelpful details are filtered out. "
            "You can review and edit saved memories there. "
            "Private chats never use or add memories.",
            page,
        )
        memory_hint.setObjectName("settingsHint")
        memory_hint.setWordWrap(True)
        form.addRow("Second Brain", self._automatic_memory)
        form.addRow("", memory_hint)
        return page

    def _build_models(self) -> QWidget:
        from llm_manager_app.model_names import ModelNames
        from llm_manager_app.widgets.model_choice import ModelChoice

        page = QWidget(self)
        self._model_dir = QLineEdit(page)
        self._model_dir.setObjectName("modelDirEdit")
        self._model_dir.setClearButtonEnabled(True)

        browse = QPushButton("Browse…", page)
        browse.setObjectName("modelDirBrowse")
        browse.clicked.connect(self._browse_model_dir)
        save = QPushButton("Save", page)
        save.setObjectName("modelDirSave")
        save.clicked.connect(self._save_model_dir)
        rescan = QPushButton("Rescan", page)
        rescan.setObjectName("modelDirRescan")
        rescan.clicked.connect(self._rescan)
        reveal = QPushButton("Reveal in file manager", page)
        reveal.setObjectName("modelDirReveal")
        reveal.clicked.connect(self._reveal_model_dir)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self._model_dir, 1)
        row.addWidget(browse)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(save)
        actions.addWidget(rescan)
        actions.addWidget(reveal)
        actions.addStretch(1)

        self._models_error = QLabel(page)
        self._models_error.setObjectName("settingsError")
        self._models_error.setWordWrap(True)
        self._models_error.hide()

        hint = QLabel("Model directory is stored in config.json, not GUI settings.", page)
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)
        self._default_model = ModelChoice(
            page, names=self._names or ModelNames(self._settings, self),
        )
        self._default_model.combo.setObjectName("defaultModelChoice")
        self._default_model.changed.connect(self._save_default_model)
        layout.addWidget(QLabel("Default model for new chats", page))
        layout.addWidget(self._default_model)
        default_hint = QLabel(
            "Used when a project has no default model. Existing chats keep their model.", page,
        )
        default_hint.setObjectName("settingsHint")
        default_hint.setWordWrap(True)
        layout.addWidget(default_hint)
        layout.addSpacing(20)
        layout.addWidget(QLabel("Model directory", page))
        layout.addLayout(row)
        layout.addLayout(actions)
        layout.addWidget(self._models_error)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def set_catalog(self, models, availability) -> None:
        self._default_model.set_catalog(models, availability)

    def _save_default_model(self, ref) -> None:
        save_default_model(self._settings, ref)
        self.default_model_changed.emit(ref)

    def _build_advanced(self) -> QWidget:
        page = QWidget(self)
        self._db_path_edit = QLineEdit(page)
        self._db_path_edit.setObjectName("dbPathEdit")
        self._db_path_edit.setReadOnly(True)

        open_log = QPushButton("Open engine log", page)
        open_log.setObjectName("openLogButton")
        open_log.clicked.connect(self._open_log)

        form = QFormLayout(page)
        form.setContentsMargins(8, 12, 8, 8)
        form.setSpacing(8)
        form.addRow("Database", self._db_path_edit)
        form.addRow("", open_log)
        return page

    def _reload_engine_paths(self) -> None:
        cfg = self._config_get()
        model_dir = str(cfg.get("model_dir", "") or "")
        blocked = self._model_dir.blockSignals(True)
        self._model_dir.setText(model_dir)
        self._model_dir.blockSignals(blocked)
        if self._db_path is not None:
            self._db_path_edit.setText(str(self._db_path))
        else:
            self._db_path_edit.setText(str(cfg.get("db_path", "") or ""))

    def _on_appearance(self, _index: int) -> None:
        theme = str(self._appearance.currentData() or "dark")
        self._settings.setValue(KEY_APPEARANCE, theme)
        self.appearance_changed.emit(theme)

    def _on_automatic_memory(self, checked: bool) -> None:
        self._settings.setValue(KEY_AUTO_MEMORY, checked)
        self.automatic_memory_changed.emit(checked)

    def _on_return_sends(self, checked: bool) -> None:
        self._settings.setValue(KEY_RETURN_SENDS, checked)
        self.return_sends_changed.emit(checked)

    def _browse_model_dir(self) -> None:
        current = self._model_dir.text().strip() or str(config.default_model_dir())
        chosen = QFileDialog.getExistingDirectory(self, "Model directory", current)
        if chosen:
            self._model_dir.setText(chosen)

    def _save_model_dir(self) -> None:
        path = self._model_dir.text().strip()
        if not path:
            self._models_error.setText("model_dir must be a non-empty path")
            self._models_error.show()
            return
        try:
            # Engine config.json; never QSettings.
            self._config_set(model_dir=path)
        except Exception as exc:
            self._models_error.setText(str(exc))
            self._models_error.show()
            return
        self._models_error.hide()
        self._reload_engine_paths()

    def _rescan(self) -> None:
        try:
            self._reload_engine_paths()
            self._models_error.hide()
        except Exception as exc:
            self._models_error.setText(str(exc))
            self._models_error.show()
            return
        self.rescan_requested.emit()

    def _reveal_model_dir(self) -> None:
        path = Path(self._model_dir.text().strip() or str(config.default_model_dir()))
        self._open_path(path)

    def _open_log(self) -> None:
        self._open_path(self._log_path)


class ShortcutsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shortcutsDialog")
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(480, 400)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for row, (seq, action) in enumerate(_SHORTCUTS):
            key = QLabel(_native(seq) if seq not in {"Return", "Escape", "Delete"} else seq)
            key.setObjectName("shortcutKey")
            label = QLabel(action)
            label.setWordWrap(True)
            grid.addWidget(key, row, 0)
            grid.addWidget(label, row, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(grid, 1)
        layout.addWidget(buttons)
