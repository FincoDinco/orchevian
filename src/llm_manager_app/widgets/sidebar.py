"""Sidebar: Chats folders (All / projects / Ungrouped) and Models. Templates stay hidden."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMenu,
    QMessageBox,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ModelRef, Project
from llm_manager_app.widgets.project_sheet import ProjectSheet

CHATS = "chats"
MODELS = "models"

FOLDER_ALL = "all"
FOLDER_PROJECT = "project"
FOLDER_UNGROUPED = "ungrouped"

_KIND_ROLE = Qt.ItemDataRole.UserRole
_ID_ROLE = Qt.ItemDataRole.UserRole + 1
_NAME_ROLE = Qt.ItemDataRole.UserRole + 2

_KIND_CHATS = "chats"
_KIND_MODELS = "models"
_KIND_ALL = "all"
_KIND_PROJECT = "project"
_KIND_UNGROUPED = "ungrouped"


@dataclass(frozen=True, slots=True)
class SidebarSelection:
    section: str
    folder: str
    project_id: int | None = None
    project_name: str | None = None


class ProjectLibrary(Protocol):
    def list_projects(self) -> list[Project]: ...
    def create_project(
        self, name: str, instructions: str = "", model: ModelRef | None = None
    ) -> Project: ...
    def delete_project(self, id: int) -> None: ...


def _item(label: str, kind: str) -> QStandardItem:
    item = QStandardItem(label)
    item.setEditable(False)
    item.setData(kind, _KIND_ROLE)
    return item


class Sidebar(QWidget):
    section_changed = Signal(str)
    filter_changed = Signal(object)
    project_created = Signal(int)
    project_deleted = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, library: ProjectLibrary) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._library = library
        self._last_section = CHATS

        self._model = QStandardItemModel(self)
        self._chats_item: QStandardItem | None = None
        self._all_item: QStandardItem | None = None
        self._ungrouped_item: QStandardItem | None = None
        self._models_item: QStandardItem | None = None
        self._project_items: dict[int, QStandardItem] = {}

        self._view = QTreeView(self)
        self._view.setObjectName("sidebarNav")
        self._view.setModel(self._model)
        self._view.setHeaderHidden(True)
        self._view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setAnimated(False)
        self._view.setIndentation(16)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        selection = self._view.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._on_current_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(0)
        layout.addWidget(self._view)

        self.refresh()

    def current_section(self) -> str:
        return self.current_selection().section

    def current_selection(self) -> SidebarSelection:
        index = self._view.currentIndex()
        if not index.isValid():
            return SidebarSelection(CHATS, FOLDER_ALL)
        return self._selection_from_index(index)

    def select_section(self, key: str) -> None:
        if key == MODELS:
            self._set_current(self._index_for(_KIND_MODELS))
            return
        if self.current_section() == CHATS:
            return
        self._set_current(self._index_for(_KIND_ALL))

    def select_all(self) -> None:
        self._set_current(self._index_for(_KIND_ALL))

    def select_ungrouped(self) -> None:
        self._set_current(self._index_for(_KIND_UNGROUPED))

    def select_project(self, project_id: int) -> None:
        self._set_current(self._index_for(_KIND_PROJECT, project_id))

    def new_project(
        self,
        name: str | None = None,
        instructions: str = "",
        model: ModelRef | None = None,
    ) -> int | None:
        if name is None:
            sheet = ProjectSheet(self)
            if sheet.exec() != ProjectSheet.DialogCode.Accepted:
                return None
            name, instructions, model = sheet.values()
        stripped = name.strip()
        if not stripped:
            return None
        project = self._library.create_project(stripped, instructions, model)
        self.refresh(select_project_id=project.id)
        self.project_created.emit(project.id)
        return project.id

    def delete_selected_project(self, *, confirmed: bool | None = None) -> None:
        selection = self.current_selection()
        if selection.folder != FOLDER_PROJECT or selection.project_id is None:
            return
        name = selection.project_name or "project"
        if confirmed is None:
            result = QMessageBox.question(
                self,
                "Delete project",
                f'Delete "{name}"? Conversations will be kept.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            confirmed = result == QMessageBox.StandardButton.Yes
        if not confirmed:
            return
        pid = selection.project_id
        self._library.delete_project(pid)
        self.refresh()
        self.project_deleted.emit(pid)

    def refresh(self, select_project_id: int | None = None) -> None:
        keep = self._current_kind_and_id()
        self._rebuild()
        if select_project_id is not None:
            index = self._index_for(_KIND_PROJECT, select_project_id)
        elif keep is not None:
            index = self._index_for(*keep)
            if not index.isValid():
                index = self._index_for(_KIND_ALL)
        else:
            index = self._index_for(_KIND_ALL)
        if not index.isValid():
            index = self._index_for(_KIND_ALL)
        self._set_current(index)

    def _rebuild(self) -> None:
        self._model.clear()
        self._project_items = {}
        root = self._model.invisibleRootItem()
        if root is None:
            return

        chats = _item("Chats", _KIND_CHATS)
        chats.setFlags(Qt.ItemFlag.ItemIsEnabled)
        all_item = _item("All", _KIND_ALL)
        chats.appendRow(all_item)
        for project in self._library.list_projects():
            item = _item(f"Project: {project.name}", _KIND_PROJECT)
            item.setData(project.id, _ID_ROLE)
            item.setData(project.name, _NAME_ROLE)
            chats.appendRow(item)
            self._project_items[project.id] = item
        ungrouped = _item("Ungrouped", _KIND_UNGROUPED)
        chats.appendRow(ungrouped)
        root.appendRow(chats)

        models = _item("Models", _KIND_MODELS)
        root.appendRow(models)

        self._chats_item = chats
        self._all_item = all_item
        self._ungrouped_item = ungrouped
        self._models_item = models
        self._view.expand(chats.index())

    def _set_current(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        self._view.setCurrentIndex(index)
        self._view.scrollTo(index)

    def _index_for(self, kind: str, project_id: int | None = None) -> QModelIndex:
        item: QStandardItem | None
        if kind == _KIND_ALL:
            item = self._all_item
        elif kind == _KIND_UNGROUPED:
            item = self._ungrouped_item
        elif kind == _KIND_MODELS:
            item = self._models_item
        elif kind == _KIND_CHATS:
            item = self._chats_item
        elif kind == _KIND_PROJECT and project_id is not None:
            item = self._project_items.get(project_id)
        else:
            item = None
        if item is None:
            return QModelIndex()
        return item.index()

    def _current_kind_and_id(self) -> tuple[str, int | None] | None:
        index = self._view.currentIndex()
        if not index.isValid():
            return None
        kind = str(index.data(_KIND_ROLE) or "")
        pid = index.data(_ID_ROLE)
        return kind, int(pid) if pid is not None else None

    def _selection_from_index(self, index: QModelIndex) -> SidebarSelection:
        kind = str(index.data(_KIND_ROLE) or "")
        if kind == _KIND_MODELS:
            return SidebarSelection(MODELS, FOLDER_ALL)
        if kind == _KIND_UNGROUPED:
            return SidebarSelection(CHATS, FOLDER_UNGROUPED)
        if kind == _KIND_PROJECT:
            pid = index.data(_ID_ROLE)
            name = index.data(_NAME_ROLE)
            return SidebarSelection(
                CHATS,
                FOLDER_PROJECT,
                project_id=int(pid) if pid is not None else None,
                project_name=str(name) if name is not None else None,
            )
        return SidebarSelection(CHATS, FOLDER_ALL)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if not current.isValid():
            return
        kind = str(current.data(_KIND_ROLE) or "")
        if kind == _KIND_CHATS:
            self._set_current(self._index_for(_KIND_ALL))
            return
        selection = self._selection_from_index(current)
        if selection.section != self._last_section:
            self._last_section = selection.section
            self.section_changed.emit(selection.section)
        if selection.section == CHATS:
            self.filter_changed.emit(selection)

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self._view.indexAt(pos)
        if index.isValid():
            self._view.setCurrentIndex(index)
        menu = QMenu(self)
        menu.addAction("New Project", lambda: self.new_project())
        selection = self.current_selection()
        if selection.folder == FOLDER_PROJECT:
            menu.addAction("Delete Project", lambda: self.delete_selected_project())
        menu.exec(self._view.viewport().mapToGlobal(pos))
