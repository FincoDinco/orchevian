"""An integrated second brain: note editor, Markdown reader, backlinks, and graph."""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote, unquote

import markdown
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llm_engine.store.vault import WIKILINK, MemoryNote, MemoryVault, split_frontmatter
from llm_manager_app.icons import icon
from llm_manager_app.tokens import current_palette, system_font_family
from llm_manager_app.widgets.labels import ElidedLabel
from llm_manager_app.widgets.memory_graph import MemoryGraph


class _Reader(QTextBrowser):
    def loadResource(self, resource_type, name):
        # A note can contain arbitrary Markdown; never fetch its images or local resources.
        return None


class MemoryView(QWidget):
    remember_requested = Signal()
    cancel_requested = Signal()
    vault_changed = Signal(object)
    recall_changed = Signal(bool)
    source_requested = Signal(int)

    def __init__(self, vault: MemoryVault, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("memoryWorkspace")
        self.vault = vault
        self._notes: list[MemoryNote] = []
        self._active: MemoryNote | None = None
        self._drafts: dict[str, tuple[MemoryNote, str]] = {}
        self._loading = False
        self._busy = False
        self._capture_available = False

        title = QLabel("Second brain", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel("A place for ideas to grow and connect.", self)
        subtitle.setObjectName("pageSubtitle")
        heading = QVBoxLayout()
        heading.addWidget(title)
        heading.addWidget(subtitle)
        header = QHBoxLayout()
        header.addLayout(heading, 1)
        self._new = QPushButton("New note", self)
        self._new.setIcon(icon("plus"))
        self._new.clicked.connect(self.new_note)
        self._remember = QPushButton("Remember chat", self)
        self._remember.setObjectName("memoryCapture")
        self._remember.setIcon(icon("brain"))
        self._remember.setToolTip(
            "Turn the selected conversation into connected notes using its model"
        )
        self._remember.clicked.connect(self.remember_requested)
        header.addWidget(self._new)
        header.addWidget(self._remember)

        self._status = QLabel("", self)
        self._status.setObjectName("memoryStatus")
        self._status.setWordWrap(True)
        self._cancel = QPushButton("Cancel", self)
        self._cancel.clicked.connect(self.cancel_requested)
        self._cancel.hide()
        status_row = QHBoxLayout()
        status_row.addWidget(self._status, 1)
        status_row.addWidget(self._cancel)

        self._search = QLineEdit(self)
        self._search.setObjectName("memorySearch")
        self._search.setPlaceholderText("Search notes, ideas, #tags…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        self._list = QListWidget(self)
        self._list.setObjectName("memoryNotes")
        self._list.setMinimumWidth(160)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.currentItemChanged.connect(self._select_item)
        self._count = QLabel(self)
        self._count.setObjectName("eyebrow")
        list_pane = QWidget(self)
        list_layout = QVBoxLayout(list_pane)
        list_layout.setContentsMargins(0, 0, 12, 0)
        list_layout.addWidget(self._search)
        list_layout.addWidget(self._count)
        list_layout.addWidget(self._list, 1)
        refresh = QPushButton("Refresh notes", self)
        refresh.setIcon(icon("refresh"))
        refresh.clicked.connect(lambda: self.refresh())
        list_layout.addWidget(refresh)

        detail = QWidget(self)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        self._title = ElidedLabel("Your ideas, connected", self)
        self._title.setObjectName("memoryTitle")
        self._path = ElidedLabel("", self)
        self._path.setObjectName("pageSubtitle")
        detail_layout.addWidget(self._title)
        detail_layout.addWidget(self._path)
        self._tabs = QTabWidget(self)
        self._reader = _Reader(self)
        self._reader.setObjectName("memoryReader")
        self._reader.setOpenLinks(False)
        self._reader.setOpenExternalLinks(False)
        self._reader.anchorClicked.connect(self._open_link)
        self._editor = QPlainTextEdit(self)
        self._editor.setObjectName("memoryEditor")
        self._editor.setPlaceholderText("Write a note. Connect ideas with [[Note title]].")
        self._editor.textChanged.connect(self._on_edit)
        self._graph = MemoryGraph(self)
        self._graph.note_activated.connect(self.open_note)
        self._tabs.addTab(self._reader, "Read")
        self._tabs.addTab(self._editor, "Edit")
        self._tabs.addTab(self._graph, "Graph")
        self._tabs.currentChanged.connect(self._on_tab)
        detail_layout.addWidget(self._tabs, 1)
        self._connections = QListWidget(self)
        self._connections.setObjectName("memoryConnections")
        self._connections.setMaximumHeight(120)
        self._connections.itemClicked.connect(self._follow_connection)
        self._connection_label = QLabel("CONNECTIONS", self)
        self._connection_label.setObjectName("eyebrow")
        detail_layout.addWidget(self._connection_label)
        detail_layout.addWidget(self._connections)
        actions = QHBoxLayout()
        self._save = QPushButton("Save note", self)
        self._save.setObjectName("memorySave")
        self._save.clicked.connect(self.save_note)
        self._reload = QPushButton("Reload", self)
        self._reload.clicked.connect(self.reload_note)
        self._trash = QPushButton("Move to trash", self)
        self._trash.clicked.connect(self.trash_note)
        self._source = QPushButton("Open conversation", self)
        self._source.clicked.connect(self._open_source)
        fit = QPushButton("Fit graph", self)
        fit.clicked.connect(self._graph.fit_graph)
        self._fit = fit
        for button in (self._save, self._reload, self._trash, self._source, fit):
            actions.addWidget(button)
        actions.addStretch()
        detail_layout.addLayout(actions)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setChildrenCollapsible(False)
        split.addWidget(list_pane)
        split.addWidget(detail)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 720])

        self._recall = QCheckBox("Use relevant memories in chats", self)
        self._recall.setToolTip(
            "Search this vault for relevant notes and include them in model context"
        )
        self._recall.toggled.connect(self.recall_changed)
        self._vault_label = ElidedLabel(str(vault.root), self)
        self._vault_label.setObjectName("pageSubtitle")
        self._choose = QPushButton("Choose vault…", self)
        self._choose.clicked.connect(self.choose_vault)
        footer = QHBoxLayout()
        footer.addWidget(self._recall)
        footer.addStretch()
        footer.addWidget(self._vault_label, 1)
        footer.addWidget(self._choose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        layout.addLayout(header)
        layout.addLayout(status_row)
        layout.addWidget(split, 1)
        layout.addLayout(footer)
        self.refresh_theme()
        self.refresh()
        self._on_tab(0)

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()

    def set_capture_available(self, available: bool) -> None:
        self._capture_available = available
        self._remember.setEnabled(available and not self._busy)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cancel.setVisible(busy)
        self._choose.setEnabled(not busy)
        self.set_capture_available(self._capture_available)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def refresh(self, select_key: str | None = None) -> None:
        self._stash()
        try:
            self._notes = self.vault.list_notes()
        except Exception as exc:
            self.set_status(f"Could not read the vault: {exc}")
            return
        self._filter(select_key=select_key or (self._active.key if self._active else None))
        self._graph.set_notes(self._notes, self.vault, self._active.key if self._active else None)

    def _filter(self, _text: str = "", *, select_key: str | None = None) -> None:
        self._stash()
        selected = select_key or (self._active.key if self._active else None)
        words = self._search.text().casefold().split()
        blocked = self._list.blockSignals(True)
        self._list.clear()
        row = -1
        for note in self._notes:
            haystack = f"{note.title}\n{note.body}\n{' '.join(note.tags)}".casefold()
            if not all(word.lstrip("#") in haystack for word in words):
                continue
            prefix = "Source · " if note.kind == "source" else ""
            item = QListWidgetItem(prefix + note.title)
            item.setData(Qt.ItemDataRole.UserRole, note.key)
            item.setToolTip(note.key)
            item.setIcon(icon("chat" if note.kind == "source" else "note"))
            self._list.addItem(item)
            if note.key == selected:
                row = self._list.count() - 1
        self._list.setCurrentRow(row if row >= 0 else (0 if self._list.count() else -1))
        self._list.blockSignals(blocked)
        self._count.setText(f"{self._list.count()} NOTES")
        self._select_item(self._list.currentItem(), None)

    def _stash(self) -> None:
        if self._active is None or self._loading:
            return
        content = self._editor.toPlainText()
        if content != self._active.content:
            self._drafts[self._active.key] = (self._active, content)
        else:
            self._drafts.pop(self._active.key, None)

    def _select_item(self, current, _previous) -> None:
        self._stash()
        key = current.data(Qt.ItemDataRole.UserRole) if current else None
        note = next((note for note in self._notes if note.key == key), None)
        content = note.content if note else ""
        if key in self._drafts:
            note, content = self._drafts[key]
        self._loading = True
        self._active = note
        self._editor.setPlainText(content)
        self._editor.setEnabled(note is not None)
        self._loading = False
        self._path.setText(note.key + ".md" if note else "")
        self._reload.setEnabled(note is not None)
        self._trash.setEnabled(note is not None)
        self._source.setVisible(note is not None and note.source_id is not None)
        self._on_edit()
        self._render()
        self._show_connections()

    def _on_edit(self) -> None:
        if self._loading:
            return
        dirty = self._active is not None and self._editor.toPlainText() != self._active.content
        self._save.setEnabled(dirty)
        title = self._active.title if self._active else "Your ideas, connected"
        self._title.setText(title + (" · Unsaved" if dirty else ""))

    def _render(self) -> None:
        palette = current_palette()
        if self._active is None:
            body = (
                "# Build your second brain\n\nCreate a note, or use **Remember chat** to let "
                "your model extract useful memories from a conversation.\n\n"
                "Connect notes with `[[Note title]]`. Follow their connections here, "
                "or explore the **Graph**."
            )
        else:
            _, body = split_frontmatter(self._editor.toPlainText())
        body = html.escape(body)
        # Preserve Markdown quote markers while keeping raw HTML escaped.
        body = re.sub(r"(?m)^(?:[ \t]*&gt;)+", lambda match: html.unescape(match[0]), body)

        def wikilink(match) -> str:
            target, _, label = html.unescape(match[1]).partition("|")
            label = label or target
            return f'<a href="memory:{quote(target, safe="")}">{html.escape(label)}</a>'

        # Convert after Markdown so wiki syntax inside code blocks remains literal.
        rendered = markdown.markdown(body, extensions=["fenced_code", "tables", "nl2br"])
        pieces = re.split(r"(<pre>.*?</pre>|<code>.*?</code>)", rendered, flags=re.DOTALL)
        rendered = "".join(
            piece if piece.startswith(("<pre>", "<code>")) else WIKILINK.sub(wikilink, piece)
            for piece in pieces
        )
        self._reader.setHtml(
            f"<html><head><style>body {{ color: {palette.text}; "
            f'font-family: "{system_font_family()}"; '
            f"font-size: 14px; }} a {{ color: {palette.accent}; }} "
            f"pre, code {{ background: {palette.elevated}; }} "
            f"blockquote {{ color: {palette.secondary}; }} "
            "p { line-height: 1.6; } h1 { font-size: 24px; } h2 { font-size: 18px; }"
            f"</style></head><body>{rendered}</body></html>"
        )

    def _show_connections(self) -> None:
        self._connections.clear()
        if self._active is None:
            return
        edges = self.vault.connections(self._notes)
        key = self._active.key
        for note in self._notes:
            incoming, outgoing = (note.key, key) in edges, (key, note.key) in edges
            if incoming or outgoing:
                prefix = "↔ " if incoming and outgoing else ("← " if incoming else "→ ")
                item = QListWidgetItem(prefix + note.title)
                item.setData(Qt.ItemDataRole.UserRole, note.key)
                item.setToolTip("Backlink" if incoming else "Linked note")
                self._connections.addItem(item)
        for target in self._active.links:
            if self.vault.resolve(target, self._notes, key) is None:
                item = QListWidgetItem("+ " + target + " (create note)")
                item.setData(Qt.ItemDataRole.UserRole, target)
                self._connections.addItem(item)
        if not self._connections.count():
            item = QListWidgetItem("No links yet. Add [[Note title]] to connect an idea.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._connections.addItem(item)

    def _follow_connection(self, item) -> None:
        target = item.data(Qt.ItemDataRole.UserRole)
        if target:
            self.open_note(target)

    def _open_link(self, url: QUrl) -> None:
        if url.scheme() == "memory":
            self.open_note(unquote(url.toString()[7:]))
        elif url.scheme() in {"http", "https", "mailto"}:
            QDesktopServices.openUrl(url)

    def open_note(self, target: str) -> None:
        note = self.vault.resolve(target, self._notes, self._active.key if self._active else "")
        if note is None:
            answer = QMessageBox.question(
                self, "Create linked note", f"Create a note named “{target}”?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                note = self.vault.create(Path(target).name)
            except Exception as exc:
                self.set_status(str(exc))
                return
        self._search.clear()
        self.refresh(select_key=note.key)
        self._tabs.setCurrentIndex(0)

    def new_note(self) -> None:
        title, accepted = QInputDialog.getText(self, "New note", "Title")
        if not accepted or not title.strip():
            return
        try:
            note = self.vault.create(title)
        except Exception as exc:
            self.set_status(str(exc))
            return
        self._search.clear()
        self.refresh(select_key=note.key)
        self._tabs.setCurrentIndex(1)
        self._editor.setFocus()
        self.set_status("Note created. Use [[Note title]] to connect it to another idea.")

    def save_note(self) -> bool:
        if self._active is None:
            return True
        try:
            saved = self.vault.save(
                self._active.key, self._editor.toPlainText(), revision=self._active.revision
            )
        except Exception as exc:
            self.set_status(str(exc))
            return False
        self._drafts.pop(saved.key, None)
        self._active = saved
        self.refresh(select_key=saved.key)
        self.set_status("Note saved.")
        return True

    def reload_note(self) -> None:
        if self._active is None:
            return
        self._stash()
        if self._active.key in self._drafts:
            result = QMessageBox.question(
                self, "Reload note", "Discard your unsaved edits and reload this note?"
            )
            if result != QMessageBox.StandardButton.Yes:
                return
        self._drafts.pop(self._active.key, None)
        key = self._active.key
        self._active = None
        self.refresh(select_key=key)

    def trash_note(self) -> None:
        if self._active is None:
            return
        result = QMessageBox.question(
            self, "Move note to trash", f"Move “{self._active.title}” to the vault’s .trash folder?"
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        try:
            self.vault.trash(self._active.key, revision=self._active.revision)
        except Exception as exc:
            self.set_status(str(exc))
            return
        self._drafts.pop(self._active.key, None)
        self._active = None
        self.refresh()
        self.set_status("Note moved to trash. Other notes keep their links.")

    def prepare_close(self) -> bool:
        self._stash()
        if not self._drafts:
            return True
        result = QMessageBox.question(
            self,
            "Unsaved notes",
            "Save your edited notes before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if result == QMessageBox.StandardButton.Cancel:
            return False
        if result == QMessageBox.StandardButton.Save:
            for key, (note, content) in list(self._drafts.items()):
                try:
                    self.vault.save(key, content, revision=note.revision)
                except Exception as exc:
                    self.set_status(str(exc))
                    return False
                self._drafts.pop(key, None)
        else:
            self._drafts.clear()
        self._active = None
        return True

    def choose_vault(self) -> None:
        if not self.prepare_close():
            return
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a second brain vault", str(self.vault.root)
        )
        if chosen:
            self.vault = MemoryVault(Path(chosen))
            self._vault_label.setText(str(self.vault.root))
            self._search.clear()
            self.refresh()
            self.vault_changed.emit(self.vault)
        else:
            self.refresh()

    def _open_source(self) -> None:
        if self._active is not None and self._active.source_id is not None:
            self.source_requested.emit(self._active.source_id)

    def _on_tab(self, index: int) -> None:
        graph = index == 2
        self._connections.setVisible(not graph)
        self._connection_label.setVisible(not graph)
        self._fit.setVisible(graph)
        for button in (self._save, self._reload, self._trash):
            button.setVisible(not graph)
        self._source.setVisible(
            not graph and self._active is not None and self._active.source_id is not None
        )
        if graph:
            self._graph.set_notes(
                self._notes, self.vault, self._active.key if self._active else None
            )
        elif index == 0:
            self._render()

    def refresh_theme(self) -> None:
        palette = current_palette()
        self.setStyleSheet(f"""
            QWidget#memoryWorkspace {{ background: {palette.canvas}; }}
            QListWidget#memoryNotes, QListWidget#memoryConnections, QTextBrowser#memoryReader,
            QPlainTextEdit#memoryEditor {{ background: {palette.canvas}; border: none; }}
            QListWidget#memoryNotes::item {{
                padding: 12px 8px; margin: 2px 0; border-radius: 8px; }}
            QListWidget#memoryNotes::item:selected {{
                background: {palette.selection}; color: {palette.text}; }}
            QListWidget#memoryConnections::item {{ padding: 4px 8px; }}
            QLabel#memoryTitle {{ font-size: 18px; font-weight: 600; }}
            QLabel#memoryStatus {{ color: {palette.secondary}; }}
            QPushButton#memoryCapture, QPushButton#memorySave {{
                background: {palette.accent}; color: {palette.canvas}; }}
            QPushButton#memoryCapture:disabled, QPushButton#memorySave:disabled {{
                background: {palette.selection}; color: {palette.secondary}; }}
            QPlainTextEdit#memoryEditor {{ padding: 12px; font-size: 14px; }}
        """)
        self._render()
        self._graph.set_notes(self._notes, self.vault, self._active.key if self._active else None)
