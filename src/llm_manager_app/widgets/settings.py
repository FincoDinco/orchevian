"""Settings window (Ctrl/Cmd+,) and keyboard shortcuts sheet."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
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
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from llm_engine import config

ORG_NAME = "llm-manager"
APP_NAME = "LLM Manager"

KEY_APPEARANCE = "appearance"
KEY_RETURN_SENDS = "return_sends"
KEY_INSPECTOR_OPEN = "inspector_open"
KEY_LAST_CONVERSATION_ID = "last_conversation_id"

_SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+N", "New chat"),
    ("Ctrl+Shift+N", "New project"),
    ("Ctrl+,", "Settings"),
    ("Ctrl+1", "Chats"),
    ("Ctrl+2", "Models"),
    ("Ctrl+3", "Templates (unbound)"),
    ("Ctrl+L", "Focus composer"),
    ("Ctrl+F", "Focus list search"),
    ("Return", "Send (Shift+Return = newline); Settings can flip to Ctrl/⌘+Return"),
    ("Escape", "Stop generation"),
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


def make_settings() -> QSettings:
    return QSettings(ORG_NAME, APP_NAME)


def _native(seq: str) -> str:
    return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)


def _open_path(path: Path) -> None:
    target = path if path.exists() else path.parent
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


class SettingsDialog(QDialog):
    appearance_changed = Signal(str)
    return_sends_changed = Signal(bool)
    rescan_requested = Signal()

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
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.resize(520, 360)

        self._settings = settings
        self._config_get = config_get or config.get
        self._config_set = config_set or config.set
        self._db_path = Path(db_path) if db_path is not None else None
        self._log_path = Path(log_path) if log_path is not None else config.default_log_path()
        self._open_path = open_path or _open_path

        tabs = QTabWidget(self)
        tabs.setObjectName("settingsTabs")
        tabs.addTab(self._build_general(), "General")
        tabs.addTab(self._build_models(), "Models")
        tabs.addTab(self._build_advanced(), "Advanced")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)
        self.reload()

    def reload(self) -> None:
        appearance = str(self._settings.value(KEY_APPEARANCE, "") or "").strip().lower()
        if appearance not in {"light", "dark"}:
            appearance = "dark"
        blocked = self._appearance.blockSignals(True)
        self._appearance.setCurrentIndex(0 if appearance == "dark" else 1)
        self._appearance.blockSignals(blocked)

        return_sends = as_bool(self._settings.value(KEY_RETURN_SENDS, True), True)
        blocked = self._return_sends.blockSignals(True)
        self._return_sends.setChecked(return_sends)
        self._return_sends.blockSignals(blocked)

        self._reload_engine_paths()
        self._models_error.hide()

    def _build_general(self) -> QWidget:
        page = QWidget(self)
        self._appearance = QComboBox(page)
        self._appearance.setObjectName("appearanceCombo")
        self._appearance.addItem("Dark", "dark")
        self._appearance.addItem("Light", "light")
        self._appearance.currentIndexChanged.connect(self._on_appearance)

        self._return_sends = QCheckBox("Return sends", page)
        self._return_sends.setObjectName("returnSendsCheck")
        self._return_sends.setToolTip(
            "When off, Ctrl/⌘+Return sends and Return inserts a newline."
        )
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
        return page

    def _build_models(self) -> QWidget:
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
        layout.addWidget(QLabel("Model directory", page))
        layout.addLayout(row)
        layout.addLayout(actions)
        layout.addWidget(self._models_error)
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

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
            # Engine config.json (K19); never QSettings.
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
