"""Grouped model preference picker which preserves unavailable saved references."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from llm_engine.domain.models import LocalModel, ModelRef
from llm_manager_app.model_names import BACKEND_ORDER, BACKEND_TITLES, ModelNames


class ModelChoice(QWidget):
    changed = Signal(object)

    def __init__(self, parent=None, *, names: ModelNames, empty: str = "No default"):
        super().__init__(parent)
        self._names = names
        self._empty = empty
        self._ref: ModelRef | None = None
        self._models: list[LocalModel] = []
        self._availability = {}
        self._ready = False
        self.combo = QComboBox(self)
        self.combo.setMinimumContentsLength(20)
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.hint = QLabel(self)
        self.hint.setTextFormat(Qt.TextFormat.PlainText)
        self.hint.setObjectName("settingsHint")
        self.hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo)
        layout.addWidget(self.hint)
        self.combo.currentIndexChanged.connect(self._choose)
        names.changed.connect(self._rebuild)
        self._rebuild()

    def current(self) -> ModelRef | None:
        return self._ref

    def set_current(self, ref: ModelRef | None) -> None:
        self._ref = ref
        self._rebuild()

    def set_catalog(self, models, availability) -> None:
        self._models = [m for m in models if isinstance(m, LocalModel)]
        self._availability = dict(availability)
        self._ready = True
        self._rebuild()

    def reason(self) -> str:
        if self._ref is None:
            return ""
        if not self._ready:
            return "Checking model availability…"
        ok, reason = self._availability.get(str(self._ref.backend), (True, None))
        if not ok:
            return reason or "Runtime unavailable. Restore it or choose another model."
        model = next((m for m in self._models if m.ref == self._ref), None)
        if model is None:
            return "Model missing. Restore it or choose another model."
        if not model.available:
            return model.unavailable_reason or "Runtime unavailable. Choose another model."
        return ""

    def _rebuild(self, *_args) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(self._empty, None)
        labels = self._names.labels(m.ref for m in self._models)
        for backend in BACKEND_ORDER:
            group = [m for m in self._models if str(m.ref.backend) == backend]
            if not group:
                continue
            self.combo.addItem(BACKEND_TITLES[backend])
            self.combo.model().item(self.combo.count() - 1).setEnabled(False)
            for model in group:
                ok = self._availability.get(backend, (True, None))[0] and model.available
                label = labels[model.ref.id] + (" — unavailable" if not ok else "")
                self.combo.addItem(label, model.ref)
                row = self.combo.count() - 1
                self.combo.setItemData(row, model.ref.id, Qt.ItemDataRole.ToolTipRole)
                self.combo.model().item(row).setEnabled(ok)
        # QVariant's opaque Python objects may compare by identity. Persisted model
        # references are fresh dataclass instances, so compare their values in Python.
        index = next(
            (i for i in range(self.combo.count()) if self.combo.itemData(i) == self._ref), -1,
        ) if self._ref else 0
        if index < 0:
            backend = BACKEND_TITLES[str(self._ref.backend)]
            state = "missing" if self._ready else "checking"
            self.combo.addItem(f"{self._names.display(self._ref)} · {backend} — {state}", self._ref)
            index = self.combo.count() - 1
            self.combo.model().item(index).setEnabled(False)
        self.combo.setCurrentIndex(index)
        self.combo.setToolTip(self._ref.id if self._ref else self._empty)
        self.combo.blockSignals(False)
        self.hint.setText(self.reason())
        self.hint.setVisible(bool(self.hint.text()))

    def _choose(self, _index) -> None:
        self._ref = self.combo.currentData()
        self.combo.setToolTip(self._ref.id if self._ref else self._empty)
        self.hint.setText(self.reason())
        self.hint.setVisible(bool(self.hint.text()))
        self.changed.emit(self._ref)
