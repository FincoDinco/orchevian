"""Column 2 conversation list: LibraryService summaries, search, New Chat, rename, delete."""

from __future__ import annotations

from datetime import datetime
from types import EllipsisType
from typing import Any, Protocol

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import Conversation, ConversationSummary, ModelRef, Project
from llm_manager_app.icons import icon
from llm_manager_app.model_names import ModelNames, friendly_name
from llm_manager_app.tokens import current_palette, qcolor

# Ellipsis = All folders; None = ungrouped.
_UNFILTERED: EllipsisType = ...
_ROW_H = 36


def relative_stamp(when: datetime, *, now: datetime | None = None) -> str:
    moment = now or datetime.now()
    seconds = int((moment - when).total_seconds())
    if seconds < 45:
        return "Now"
    if seconds < 3600:
        return f"{max(1, seconds // 60)}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}d"
    return f"{when.strftime('%b')} {when.day}"


def row_meta(summary: ConversationSummary, names: ModelNames | None = None) -> str:
    stamp = relative_stamp(summary.updated_at)
    if summary.model is None:
        return stamp
    name = names.display(summary.model) if names else friendly_name(summary.model.name)
    return f"{stamp} · {name}"


class ConversationDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        del option, index
        return QSize(160, _ROW_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = current_palette()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(4, 1, -4, -1)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(palette.selection))
            painter.drawRoundedRect(rect, palette.radius_control, palette.radius_control)
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        title_rect = QRect(rect.x() + 10, rect.y(), rect.width() - 20, rect.height())
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Weight.Normal)
        painter.setFont(title_font)
        painter.setPen(QColor(palette.text))
        elided = painter.fontMetrics().elidedText(
            title, Qt.TextElideMode.ElideRight, title_rect.width()
        )
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
            elided,
        )
        painter.restore()


class ConversationStore(Protocol):
    def list_conversations(
        self,
        project_id: int | None | EllipsisType = ...,
        query: str | None = None,
    ) -> list[ConversationSummary]: ...
    def create_conversation(
        self, project_id: int | None = None, model: ModelRef | None = None
    ) -> Conversation: ...
    def rename(self, id: int, title: str) -> None: ...
    def delete_conversation(self, id: int) -> None: ...
    def move(self, id: int, project_id: int | None) -> None: ...
    def list_projects(self) -> list[Project]: ...
    def create_project(
        self, name: str, instructions: str = "", model: ModelRef | None = None
    ) -> Project: ...
    def delete_project(self, id: int) -> None: ...


class ConversationListModel(QAbstractListModel):
    IdRole = Qt.ItemDataRole.UserRole
    SummaryRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent: QWidget | None = None, *, names: ModelNames | None = None) -> None:
        super().__init__(parent)
        self._names = names
        self._rows: list[ConversationSummary] = []

    def set_rows(self, rows: list[ConversationSummary]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title
        if role == self.IdRole:
            return row.id
        if role == self.SummaryRole:
            return row
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"{row.title}\n{row_meta(row, self._names)}"
        return None

    def summary_at(self, row: int) -> ConversationSummary | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def index_for_id(self, cid: int) -> QModelIndex:
        for row, summary in enumerate(self._rows):
            if summary.id == cid:
                return self.index(row)
        return QModelIndex()


class ConversationList(QWidget):
    selected_id_changed = Signal(object)
    chat_created = Signal(int)
    conversation_activated = Signal()

    def __init__(
        self, parent: QWidget | None = None, *, library: ConversationStore,
        names: ModelNames | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("listPane")
        self._library = library
        self._project_id: int | None | EllipsisType = _UNFILTERED
        self._project_name: str | None = None

        self._search = QLineEdit(self)
        self._search.setObjectName("conversationSearch")
        self._search.setPlaceholderText("Search conversations")
        self._search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)

        self._new_btn = QPushButton("+ New Chat", self)
        self._new_btn.setObjectName("newChatButton")
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._new_btn.clicked.connect(lambda: self.new_chat())

        self._model = ConversationListModel(self, names=names)
        self._view = QListView(self)
        self._view.setObjectName("conversationView")
        self._view.setModel(self._model)
        self._view.setItemDelegate(ConversationDelegate(self._view))
        self._view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._view.setUniformItemSizes(True)
        self._view.setSpacing(1)
        self._view.setMouseTracking(True)
        self._view.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._on_context_menu)
        self._view.doubleClicked.connect(lambda *_: self.rename_selected())
        self._view.clicked.connect(lambda *_: self.conversation_activated.emit())
        selection = self._view.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(lambda *_: self._emit_selection())

        self._empty = QLabel(self)
        self._empty.setObjectName("listEmpty")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._empty)

        self._heading = QLabel("Recents", self)
        self._heading.setObjectName("sidebarSection")

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header.addWidget(self._search, 1)
        header.addWidget(self._new_btn, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(self._heading)
        layout.addWidget(self._stack, 1)

        self._shortcut_rename = QShortcut(QKeySequence("F2"), self._view)
        self._shortcut_rename.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._shortcut_rename.activated.connect(lambda: self.rename_selected())
        self._shortcut_delete = QShortcut(QKeySequence.StandardKey.Delete, self._view)
        self._shortcut_delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._shortcut_delete.activated.connect(lambda: self.delete_selected())
        self._shortcut_backspace = QShortcut(QKeySequence(Qt.Key.Key_Backspace), self._view)
        self._shortcut_backspace.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._shortcut_backspace.activated.connect(lambda: self.delete_selected())

        self.refresh()

    def set_embedded(self) -> None:
        self._new_btn.hide()
        self._search.hide()
        self.layout().setContentsMargins(4, 16, 4, 0)

    def set_project_filter(
        self,
        project_id: int | None | EllipsisType = _UNFILTERED,
        *,
        project_name: str | None = None,
    ) -> None:
        if project_id == self._project_id and project_name == self._project_name:
            return
        self._project_id = project_id
        self._project_name = project_name
        self._heading.setText(project_name or "Recents")
        self.refresh()

    def focus_search(self) -> None:
        self._search.show()
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    def selected_id(self) -> int | None:
        summary = self.selected_summary()
        return None if summary is None else summary.id

    def selected_title(self) -> str | None:
        summary = self.selected_summary()
        if summary is None:
            return None
        title = summary.title.strip()
        return title or None

    def selected_summary(self) -> ConversationSummary | None:
        index = self._view.currentIndex()
        if not index.isValid():
            return None
        return self._model.summary_at(index.row())

    def select_id(self, cid: int | None) -> None:
        if cid is None:
            self._view.clearSelection()
            self._view.setCurrentIndex(QModelIndex())
            return
        index = self._model.index_for_id(cid)
        if index.isValid():
            self._view.setCurrentIndex(index)
            self._view.scrollTo(index)
        else:
            self._view.clearSelection()
            self._view.setCurrentIndex(QModelIndex())

    def refresh(self, select_id: int | None = None) -> None:
        keep = self.selected_id() if select_id is None else select_id
        query = self._search.text().strip() or None
        # Summaries only — never get_conversation / messages.
        if self._project_id is _UNFILTERED:
            rows = self._library.list_conversations(query=query)
        else:
            rows = self._library.list_conversations(project_id=self._project_id, query=query)
        self._model.set_rows(rows)
        self._sync_empty(query, rows)
        if keep is not None and self._model.index_for_id(keep).isValid():
            self.select_id(keep)
        elif query is None and rows:
            self.select_id(rows[0].id)
        else:
            self.select_id(None)
        self._emit_selection()

    def new_chat(self, model: ModelRef | None = None) -> int:
        pid = self._project_id if isinstance(self._project_id, int) else None
        created = self._library.create_conversation(project_id=pid, model=model)
        cid = created.summary.id
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self.refresh(select_id=cid)
        self.chat_created.emit(cid)
        return cid

    def rename_selected(self, title: str | None = None) -> None:
        summary = self.selected_summary()
        if summary is None:
            return
        if title is None:
            typed, ok = QInputDialog.getText(
                self,
                "Rename",
                "Conversation title:",
                text=summary.title,
            )
            if not ok:
                return
            title = str(typed)
        stripped = title.strip()
        if not stripped:
            return
        self._library.rename(summary.id, stripped)
        self.refresh(select_id=summary.id)

    def delete_selected(self, *, confirmed: bool | None = None) -> None:
        summary = self.selected_summary()
        if summary is None:
            return
        if confirmed is None:
            result = QMessageBox.question(
                self,
                "Delete conversation",
                f'Delete "{summary.title}"?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            confirmed = result == QMessageBox.StandardButton.Yes
        if not confirmed:
            return
        self._library.delete_conversation(summary.id)
        self.refresh()

    def move_selected(self, project_id: int | None) -> None:
        summary = self.selected_summary()
        if summary is None:
            return
        self._library.move(summary.id, project_id)
        self.refresh()

    def _on_search(self, _text: str) -> None:
        self.refresh()

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self._view.indexAt(pos)
        if index.isValid():
            self._view.setCurrentIndex(index)
        if self.selected_id() is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self.rename_selected())
        move_menu = menu.addMenu("Move to")
        move_menu.addAction("Move out of project", lambda: self.move_selected(None))
        for project in self._library.list_projects():
            move_menu.addAction(
                f"Project: {project.name}",
                lambda pid=project.id: self.move_selected(pid),
            )
        menu.addAction("Delete", lambda: self.delete_selected())
        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _sync_empty(self, query: str | None, rows: list[ConversationSummary]) -> None:
        if rows:
            self._stack.setCurrentWidget(self._view)
            return
        if query:
            self._empty.setText("No matching conversations.")
        elif isinstance(self._project_id, int) and self._project_name:
            self._empty.setText(f"New Chat in {self._project_name}")
        else:
            shortcut = QKeySequence(QKeySequence.StandardKey.New).toString(
                QKeySequence.SequenceFormat.NativeText
            )
            self._empty.setText(f"No conversations yet.\nPress {shortcut} to start a new chat.")
        self._stack.setCurrentWidget(self._empty)

    def _emit_selection(self) -> None:
        self.selected_id_changed.emit(self.selected_id())
