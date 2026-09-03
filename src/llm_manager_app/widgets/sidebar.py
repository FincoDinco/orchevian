"""Sidebar: Chats / Models navigation. Templates wait for a later PR."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout, QWidget

CHATS = "chats"
MODELS = "models"


class Sidebar(QWidget):
    section_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")

        self._list = QListWidget(self)
        self._list.setObjectName("sidebarNav")
        self._list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for key, label in ((CHATS, "Chats"), (MODELS, "Models")):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.currentItemChanged.connect(self._emit_section)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)
        layout.addWidget(self._list)

    def current_section(self) -> str:
        item = self._list.currentItem()
        if item is None:
            return CHATS
        return str(item.data(Qt.ItemDataRole.UserRole) or CHATS)

    def select_section(self, key: str) -> None:
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                self._list.setCurrentRow(row)
                return

    def _emit_section(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is not None:
            self.section_changed.emit(str(current.data(Qt.ItemDataRole.UserRole)))
