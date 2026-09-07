"""Sidebar: Chats folders (All / projects / Ungrouped) and Models."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ModelRef
from llm_manager_app.icons import icon
from llm_manager_app.tokens import named_tab_width
from llm_manager_app.widgets.conversation_list import ConversationStore
from llm_manager_app.widgets.project_sheet import ProjectSheet

_TAB_NAMES = ("Chats", "Models", "Memory")
_EXPANDED_MIN = 260

CHATS = "chats"
MODELS = "models"
MEMORY = "memory"

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
    collapsed_changed = Signal(bool)
    new_chat_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None, *, library: ConversationStore) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._library = library
        self._last_section = CHATS
        self._collapsed = False
        self._conversations: QWidget | None = None

        self._model = QStandardItemModel(self)
        self._chats_item: QStandardItem | None = None
        self._all_item: QStandardItem | None = None
        self._ungrouped_item: QStandardItem | None = None
        self._models_item: QStandardItem | None = None
        self._memory_item: QStandardItem | None = None
        self._project_items: dict[int, QStandardItem] = {}

        self._collapse_btn = QToolButton(self)
        self._collapse_btn.setObjectName("sidebarCollapse")
        self._collapse_btn.setAutoRaise(True)
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._collapse_btn.clicked.connect(self.toggle_collapsed)

        self._view = QTreeView(self)
        self._view.setObjectName("sidebarNav")
        self._view.setModel(self._model)
        self._view.setHeaderHidden(True)
        self._view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setAnimated(False)
        self._view.setRootIsDecorated(False)
        self._view.setItemsExpandable(False)
        self._view.setIndentation(14)
        self._view.setUniformRowHeights(True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setMouseTracking(True)
        self._view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        selection = self._view.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._on_current_changed)

        header = QHBoxLayout()
        header.setContentsMargins(18, 10, 12, 16)
        self._brand = QLabel("LLM Manager", self)
        self._brand.setObjectName("brandTitle")
        header.addWidget(self._brand)
        header.addStretch(1)
        self._project_btn = QToolButton(self)
        self._project_btn.setIcon(icon("folder"))
        self._project_btn.setToolTip("New project")
        self._project_btn.setAccessibleName("New project")
        self._project_btn.setObjectName("newProjectButton")
        self._project_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._project_btn.clicked.connect(lambda: self.new_project())
        header.addWidget(self._project_btn)
        header.addWidget(self._collapse_btn)

        self._new_chat = QPushButton("New conversation", self)
        self._new_chat.setObjectName("sidebarNewChat")
        self._new_chat.setIcon(icon("plus"))
        self._new_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_chat.clicked.connect(self.new_chat_requested)

        self._settings_btn = QPushButton("Settings", self)
        self._settings_btn.setObjectName("sidebarSettings")
        self._settings_btn.setIcon(icon("settings"))
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(self.settings_requested)
        self._local_label = QLabel("LOCAL WORKSPACE", self)
        self._local_label.setObjectName("eyebrow")
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 12, 16, 4)
        footer.addWidget(self._settings_btn)
        footer.addStretch()
        footer.addWidget(self._local_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)
        layout.addLayout(header)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(16, 0, 16, 4)
        button_row.addWidget(self._new_chat)
        layout.addLayout(button_row)
        layout.addWidget(self._view)
        layout.addStretch(1)
        layout.addLayout(footer)
        self._layout = layout

        self.refresh()
        self._apply_collapsed()

    def attach_conversations(self, conversations: QWidget) -> None:
        self._conversations = conversations
        self._layout.takeAt(3)
        self._layout.insertWidget(3, conversations, 1)
        self._layout.insertStretch(4, 0)
        self._apply_collapsed()

    def current_section(self) -> str:
        return self.current_selection().section

    def is_collapsed(self) -> bool:
        return self._collapsed

    def tab_width(self) -> int:
        # Include the row icon, spacing, padding, and outer margins.
        return named_tab_width(self, _TAB_NAMES, extra=76)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            self._apply_collapsed()
            return
        self._collapsed = collapsed
        self._apply_collapsed()
        self.collapsed_changed.emit(collapsed)

    def current_selection(self) -> SidebarSelection:
        index = self._view.currentIndex()
        if not index.isValid():
            return SidebarSelection(CHATS, FOLDER_ALL)
        return self._selection_from_index(index)

    def select_section(self, key: str) -> None:
        if key in {MODELS, MEMORY}:
            self._set_current(self._index_for(key))
            return
        if self.current_section() == CHATS:
            return
        if self._collapsed:
            self._set_current(self._index_for(_KIND_CHATS))
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
        self._apply_collapsed()

    def _rebuild(self) -> None:
        self._model.clear()
        self._chats_item = self._all_item = self._ungrouped_item = self._models_item = None
        self._project_items = {}
        root = self._model.invisibleRootItem()
        if root is None:
            return

        chats = _item("Chats", _KIND_CHATS)
        chats.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = chats.font()
        font.setBold(True)
        chats.setFont(font)
        all_item = _item("All conversations", _KIND_ALL)
        all_item.setIcon(icon("chat"))
        chats.appendRow(all_item)
        project_items: dict[int, QStandardItem] = {}
        for project in self._library.list_projects():
            item = _item(project.name, _KIND_PROJECT)
            item.setIcon(icon("folder"))
            item.setData(project.id, _ID_ROLE)
            item.setData(project.name, _NAME_ROLE)
            chats.appendRow(item)
            project_items[project.id] = item
        ungrouped = _item("Ungrouped", _KIND_UNGROUPED)
        ungrouped.setIcon(icon("folder"))
        chats.appendRow(ungrouped)
        models = _item("Models", _KIND_MODELS)
        models.setIcon(icon("models"))
        models.setFont(font)

        self._chats_item = chats
        self._all_item = all_item
        self._ungrouped_item = ungrouped
        self._models_item = models
        self._project_items = project_items
        root.appendRow(chats)
        root.appendRow(models)
        memory = _item("Memory" if self._collapsed else "Second brain", MEMORY)
        memory.setIcon(icon("brain"))
        memory.setFont(font)
        self._memory_item = memory
        root.appendRow(memory)
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
        elif kind == MEMORY:
            item = self._memory_item
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
        if kind in {_KIND_MODELS, MEMORY}:
            return SidebarSelection(kind, FOLDER_ALL)
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
            if self._collapsed:
                selection = SidebarSelection(CHATS, FOLDER_ALL)
                if selection.section != self._last_section:
                    self._last_section = selection.section
                    self.section_changed.emit(selection.section)
                self.filter_changed.emit(selection)
                return
            prev_kind = str(_previous.data(_KIND_ROLE) or "") if _previous.isValid() else ""
            if prev_kind in {_KIND_ALL, _KIND_PROJECT, _KIND_UNGROUPED}:
                self._set_current(_previous)
            else:
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

    def _apply_collapsed(self) -> None:
        self._brand.setVisible(not self._collapsed)
        self._project_btn.setVisible(not self._collapsed)
        self._new_chat.setVisible(not self._collapsed)
        self._local_label.setVisible(not self._collapsed)
        if self._conversations is not None:
            self._conversations.setVisible(not self._collapsed)
            self._layout.setStretch(4, 1 if self._collapsed else 0)
        if self._memory_item is not None:
            self._memory_item.setText("Memory" if self._collapsed else "Second brain")
        rows = 3 if self._collapsed else 5 + len(self._project_items)
        self._view.setFixedHeight(min(300, rows * 38 + 8))
        rail = self.tab_width()
        chats = self._chats_item
        if chats is not None:
            parent = chats.index()
            for row in range(chats.rowCount()):
                self._view.setRowHidden(row, parent, self._collapsed)
            if self._collapsed:
                chats.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            else:
                chats.setFlags(Qt.ItemFlag.ItemIsEnabled)
        if self._collapsed:
            self.setMinimumWidth(rail)
            self.setMaximumWidth(rail)
            self._collapse_btn.setArrowType(Qt.ArrowType.RightArrow)
            self._collapse_btn.setToolTip("Expand sidebar")
            self._view.setIndentation(0)
            if self.current_section() == CHATS:
                self._set_current(self._index_for(_KIND_CHATS))
            return
        self.setMinimumWidth(max(_EXPANDED_MIN, rail))
        self.setMaximumWidth(16777215)
        self._collapse_btn.setArrowType(Qt.ArrowType.LeftArrow)
        self._collapse_btn.setToolTip("Collapse sidebar")
        self._view.setIndentation(14)
        current_kind = str(self._view.currentIndex().data(_KIND_ROLE) or "")
        if current_kind == _KIND_CHATS:
            self._set_current(self._index_for(_KIND_ALL))
