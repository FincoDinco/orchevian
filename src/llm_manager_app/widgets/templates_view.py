"""Reusable instructions and starter messages, saved through LibraryService."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from llm_engine.store.library import LibraryService


class TemplatesView(QWidget):
    use_requested = Signal(int)

    def __init__(self, parent=None, *, library: LibraryService) -> None:
        super().__init__(parent)
        self.setObjectName("templatesView")
        self._library = library
        self._id = None
        self._dirty = False
        self._loading = False
        self._editing = False
        title = QLabel("Prompt templates", self)
        title.setObjectName("pageTitle")
        subtitle = QLabel("Keep useful instructions and starter messages ready for your next chat.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        new = QPushButton("New template", self)
        new.setObjectName("newTemplate")
        new.clicked.connect(self.new_template)
        header = QHBoxLayout()
        header.addWidget(title, 1)
        header.addWidget(new)
        self._search = QLineEdit(self)
        self._search.setObjectName("templateSearch")
        self._search.setPlaceholderText("Search templates")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        self._list = QListWidget(self)
        self._list.setObjectName("templatesList")
        self._list.currentItemChanged.connect(self._select)
        listing = QWidget(self)
        left = QVBoxLayout(listing)
        left.setContentsMargins(0, 0, 16, 0)
        left.addWidget(self._search)
        left.addWidget(self._list, 1)
        self._empty = QLabel("No templates yet. Create one to reuse your best prompts.", self)
        self._empty.setWordWrap(True)
        left.addWidget(self._empty)
        self._editor = QWidget(self)
        editor = QVBoxLayout(self._editor)
        editor.setContentsMargins(8, 0, 0, 0)
        self._name = QLineEdit(self)
        self._name.setObjectName("templateName")
        self._name.setPlaceholderText("e.g. Review a draft")
        self._description = QLineEdit(self)
        self._description.setObjectName("templateDescription")
        self._description.setPlaceholderText("When to use this template")
        self._guidance = QPlainTextEdit(self)
        self._guidance.setObjectName("templateGuidance")
        self._guidance.setPlaceholderText("Instructions for the assistant, such as tone or format.")
        self._prompt = QPlainTextEdit(self)
        self._prompt.setObjectName("templatePrompt")
        self._prompt.setPlaceholderText("A starter message you can edit before sending.")
        for label, widget in (
            ("Name", self._name),
            ("Description (optional)", self._description),
            ("Chat guidance", self._guidance),
            ("Starter message", self._prompt),
        ):
            editor.addWidget(QLabel(label, self))
            editor.addWidget(widget, 1 if isinstance(widget, QPlainTextEdit) else 0)
            widget.textChanged.connect(self._changed)
        self._status = QLabel(self)
        self._status.setObjectName("templateStatus")
        self._status.setWordWrap(True)
        self._save = QPushButton("Save", self)
        self._save.setObjectName("saveTemplate")
        self._save.clicked.connect(self.save)
        self._delete = QPushButton("Delete…", self)
        self._delete.setObjectName("deleteTemplate")
        self._delete.clicked.connect(self.delete_selected)
        self._use = QPushButton("Use in new chat", self)
        self._use.setObjectName("useTemplate")
        self._use.clicked.connect(self.use_selected)
        actions = QHBoxLayout()
        actions.addWidget(self._delete)
        actions.addStretch()
        actions.addWidget(self._save)
        actions.addWidget(self._use)
        editor.addWidget(self._status)
        editor.addLayout(actions)
        splitter = QSplitter(self)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(listing)
        splitter.addWidget(self._editor)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([270, 560])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(subtitle)
        layout.addWidget(splitter, 1)
        self.refresh()
        # Open on a blank template: typing starts one, and Save creates it.
        self._populate()

    def refresh(self) -> None:
        with QSignalBlocker(self._list):
            self._list.clear()
            for template in self._library.list_templates():
                item = QListWidgetItem(template.name)
                item.setData(Qt.ItemDataRole.UserRole, template.id)
                item.setToolTip(template.description)
                self._list.addItem(item)
                if template.id == self._id:
                    self._list.setCurrentItem(item)
        self._filter()

    def _filter(self) -> None:
        query = self._search.text().strip().casefold()
        visible = 0
        for index in range(self._list.count()):
            item = self._list.item(index)
            match = query in (item.text() + " " + item.toolTip()).casefold()
            item.setHidden(not match)
            visible += int(match)
        self._empty.setVisible(visible == 0)
        self._empty.setText(
            "No matching templates."
            if self._list.count()
            else "No templates yet. Create one to reuse your best prompts."
        )

    def _select(self, current, previous) -> None:
        if current is None:
            return
        id = current.data(Qt.ItemDataRole.UserRole)
        if id == self._id:
            return
        if not self.prepare_close():
            with QSignalBlocker(self._list):
                self._list.setCurrentItem(previous)
            return
        try:
            template = self._library.get_template(id)
        except Exception as exc:
            self._status.setText(str(exc))
            return
        self._populate(template)
        self.refresh()

    def _populate(self, template=None) -> None:
        self._loading = True
        self._id = template.id if template else None
        self._name.setText(template.name if template else "")
        self._description.setText(template.description if template else "")
        self._guidance.setPlainText(template.system_prompt if template else "")
        self._prompt.setPlainText(template.user_prompt if template else "")
        self._loading = False
        self._editing = True
        self._dirty = False
        self._status.clear()
        self._sync()

    def new_template(self) -> None:
        if not self.prepare_close():
            return
        with QSignalBlocker(self._list):
            self._list.setCurrentRow(-1)
        self._populate()
        self._name.setFocus()

    def _changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._status.setText("Unsaved changes")
        self._sync()

    def _sync(self) -> None:
        self._editor.setEnabled(self._editing)
        self._save.setEnabled(self._dirty)
        self._use.setEnabled(bool(self._name.text().strip()))
        self._delete.setEnabled(self._id is not None)

    def save(self) -> bool:
        try:
            template = self._library.save_template(
                id=self._id,
                name=self._name.text(),
                description=self._description.text(),
                system_prompt=self._guidance.toPlainText(),
                user_prompt=self._prompt.toPlainText(),
            )
        except Exception as exc:
            self._status.setText(str(exc))
            return False
        self._id = template.id
        self._dirty = False
        self.refresh()
        self._status.setText("Saved")
        self._sync()
        return True

    def use_selected(self) -> None:
        if (self._dirty or self._id is None) and not self.save():
            return
        self.use_requested.emit(self._id)

    def delete_selected(self, *, confirmed: bool = False) -> None:
        if self._id is None:
            return
        if not confirmed:
            answer = QMessageBox.question(
                self,
                "Delete template",
                "Delete this template? Existing chats will be kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self._library.delete_template(self._id)
        except Exception as exc:
            self._status.setText(str(exc))
            return
        self._populate()  # Back to a blank template, ready to type.
        self.refresh()

    def prepare_close(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved template",
            "Save your template changes?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()
