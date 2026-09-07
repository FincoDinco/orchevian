"""Toolbar model picker: QToolButton + QMenu grouped by backend."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from llm_engine.domain.models import LocalModel, ModelRef

_BACKEND_ORDER = ("ollama", "mlx", "gguf")
_BACKEND_TITLES = {"ollama": "Ollama", "mlx": "MLX", "gguf": "GGUF"}
_SELECT = "Select a model"
_MANAGE = "Manage Models…"


def _title(backend: str) -> str:
    return _BACKEND_TITLES.get(backend, backend.upper() if len(backend) <= 4 else backend.title())


def _backend_keys(
    models: list[LocalModel],
    availability: dict[str, tuple[bool, str | None]],
) -> list[str]:
    keys = list(availability)
    seen = set(keys)
    for model in models:
        key = str(model.ref.backend)
        if key not in seen:
            keys.append(key)
            seen.add(key)

    def rank(key: str) -> tuple[int, str]:
        try:
            return _BACKEND_ORDER.index(key), key
        except ValueError:
            return len(_BACKEND_ORDER), key

    return sorted(keys, key=rank)


class ModelPicker(QToolButton):
    model_selected = Signal(object)
    manage_models_requested = Signal()
    catalog_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("modelPicker")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setAutoRaise(True)
        self.setArrowType(Qt.ArrowType.DownArrow)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        self._current: ModelRef | None = None
        self._models: list[LocalModel] = []
        self._availability: dict[str, tuple[bool, str | None]] = {}

        self._menu = QMenu(self)
        self._menu.setObjectName("modelPickerMenu")
        self._menu.aboutToShow.connect(self._on_about_to_show)
        self.setMenu(self._menu)
        self.set_current(None)
        self._rebuild_menu()

    def current(self) -> ModelRef | None:
        return self._current

    def has_models(self) -> bool:
        return bool(self._models)

    def unavailable_copy(self) -> str | None:
        ollama = self._availability.get("ollama")
        if ollama is not None and not ollama[0]:
            return ollama[1] or "Ollama is unavailable"
        for _key, (ok, reason) in self._availability.items():
            if not ok and reason:
                return reason
        return None

    def set_current(self, ref: ModelRef | None) -> None:
        self._current = ref
        if ref is None:
            self.setText(_SELECT)
            self.setToolTip(_SELECT)
        else:
            self.setText(ref.name)
            self.setToolTip(ref.id)

    def set_catalog(
        self,
        models: list[LocalModel],
        availability: dict[str, tuple[bool, str | None]],
    ) -> None:
        self._models = list(models)
        self._availability = dict(availability)
        # clear() while the popup is open dismisses it.
        if not self._menu.isVisible():
            self._rebuild_menu()

    def _on_about_to_show(self) -> None:
        self._rebuild_menu()
        self.catalog_requested.emit()

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        keys = _backend_keys(self._models, self._availability)
        for key in keys:
            self._menu.addSection(_title(key))
            ok, reason = self._availability.get(key, (True, None))
            if not ok:
                action = self._menu.addAction(reason or f"{_title(key)} unavailable")
                action.setEnabled(False)
                continue
            grouped = [model for model in self._models if str(model.ref.backend) == key]
            if not grouped:
                action = self._menu.addAction("No models")
                action.setEnabled(False)
                continue
            for model in grouped:
                action = self._menu.addAction(model.ref.name)
                action.setCheckable(True)
                action.setData(model.ref)
                group.addAction(action)
                if self._current is not None and model.ref == self._current:
                    action.setChecked(True)
                action.triggered.connect(self._emit_selected)
        if keys:
            self._menu.addSeparator()
        manage = self._menu.addAction(_MANAGE)
        manage.setObjectName("manageModels")
        manage.triggered.connect(lambda *_: self.manage_models_requested.emit())

    def _emit_selected(self, _checked: bool = False) -> None:
        action = self.sender()
        if not isinstance(action, QAction):
            return
        ref = action.data()
        if not isinstance(ref, ModelRef):
            return
        self.set_current(ref)
        self.model_selected.emit(ref)
