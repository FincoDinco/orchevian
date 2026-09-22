"""Toolbar model picker: QToolButton + QMenu grouped by backend."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QMenu,
    QToolButton,
    QWidget,
)

from llm_engine.domain.models import LocalModel, ModelRef
from llm_manager_app.model_names import BACKEND_ORDER, BACKEND_TITLES, ModelNames
from llm_manager_app.tokens import current_palette, qcolor

_BACKEND_ORDER = BACKEND_ORDER
_BACKEND_TITLES = BACKEND_TITLES
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

    def __init__(self, parent: QWidget | None = None, *, names: ModelNames | None = None) -> None:
        super().__init__(parent)
        self._names = names or ModelNames(parent=self)
        self._names.changed.connect(self.refresh_names)
        self.setObjectName("modelPicker")
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setAutoRaise(True)
        self.setArrowType(Qt.ArrowType.DownArrow)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(34)
        self.setMinimumWidth(120)

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

    def paintEvent(self, _event) -> None:
        # Paint all three elements against one center line. Native toolbutton
        # styles position menu arrows and text differently on macOS.
        palette = current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.underMouse() or self.isDown() or self.hasFocus():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(palette.selection))
            painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(self.font())
        painter.setPen(qcolor(palette.secondary))
        right = self.width() - 24
        if self._current is not None:
            backend = _title(str(self._current.backend))
            width = painter.fontMetrics().horizontalAdvance(backend) + 12
            badge = QRectF(right - width, 6, width, self.height() - 12)
            painter.setBrush(qcolor(palette.elevated))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(qcolor(palette.secondary))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, backend)
            right = int(badge.left()) - 6
        title_rect = QRectF(8, 0, max(0, right - 8), self.height())
        title = painter.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, int(title_rect.width()),
        )
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter, title)
        painter.setPen(QPen(qcolor(palette.secondary), 1.4))
        x, y = self.width() - 12, self.height() / 2
        painter.drawLine(QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5))
        painter.drawLine(QPointF(x, y + 1.5), QPointF(x + 3, y - 1.5))

    def sizeHint(self) -> QSize:
        width = self.fontMetrics().horizontalAdvance(self.text()) + 32
        if self._current is not None:
            width += self.fontMetrics().horizontalAdvance(_title(str(self._current.backend))) + 18
        return QSize(width, 34)

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
            self.setText(self._names.display(ref))
            self.setToolTip(ref.id)
        self.setAccessibleName(
            f"{self.text()}, {_title(str(ref.backend))}" if ref else _SELECT
        )
        self.updateGeometry()
        self.update()

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

    def refresh_names(self, *_args: object) -> None:
        self.set_current(self._current)
        if not self._menu.isVisible():
            self._rebuild_menu()

    def _rebuild_menu(self) -> None:
        self._menu.clear()
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        keys = _backend_keys(self._models, self._availability)
        labels = self._names.labels(model.ref for model in self._models)
        for key in keys:
            if self._menu.actions():
                self._menu.addSeparator()
            # addSection is only a separator hint: macOS styles can omit its
            # title. A disabled normal action keeps every backend heading visible.
            heading = self._menu.addAction(_title(key))
            heading.setObjectName(f"backendHeading_{key}")
            heading.setEnabled(False)
            font = QFont(self.font())
            font.setBold(True)
            heading.setFont(font)
            ok, reason = self._availability.get(key, (True, None))
            if not ok:
                action = self._menu.addAction(reason or f"{_title(key)} unavailable")
                action.setEnabled(False)
                continue
            grouped = sorted(
                (model for model in self._models if str(model.ref.backend) == key),
                key=lambda model: labels[model.ref.id].casefold(),
            )
            if not grouped:
                action = self._menu.addAction("No models")
                action.setEnabled(False)
                continue
            for model in grouped:
                action = self._menu.addAction(f"{labels[model.ref.id]} · {_title(key)}")
                if model.supports_images:
                    action.setText(action.text() + " · Vision")
                capability = ("Vision" if model.supports_images else "Vision capability unknown"
                              if model.supports_images is None else "Text only")
                action.setToolTip(f"{model.ref.id}\n{capability}")
                action.setEnabled(model.available)
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
