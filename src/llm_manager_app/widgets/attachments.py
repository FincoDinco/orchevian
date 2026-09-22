"""Chat and project file controls; parsing runs outside the UI and model workers."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.errors import EngineError
from llm_engine.services.visuals import page_selection

FILTER = (
    "Documents and pictures (*.pdf *.docx *.xlsx *.csv *.tsv *.txt *.md *.png *.jpg *.jpeg *.webp)"
)


class ImportThread(QThread):
    progress = Signal(str)

    def __init__(self, service, cid, paths, parent=None, *, importer=None, pages=None):
        super().__init__(parent)
        self.service, self.cid, self.paths = service, cid, paths
        self.cancel = threading.Event()
        self.errors = []
        self.importer = importer or service.import_file
        self.pages = pages

    def run(self):
        for path in self.paths:
            if self.cancel.is_set():
                break
            self.progress.emit(f"Reading {Path(path).name}…")
            try:
                self.importer(
                    self.cid, path, self.cancel, pages=self.pages, progress=self.progress.emit
                )
            except Exception as exc:
                if not self.cancel.is_set():
                    self.errors.append(f"{Path(path).name}: {exc}")


class AttachmentPanel(QWidget):
    busy_changed = Signal()
    files_changed = Signal()

    def __init__(self, parent=None, *, project=False):
        super().__init__(parent)
        self.service = None
        self.cid = None
        self.thread = None
        self._errors = {}
        self._dialogs = []
        self._generation = False
        self._has_documents = False
        self.project_scope = project
        self._file_selector = None
        self._selector_versions = ()
        self.setObjectName("attachmentPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(5)
        row = QHBoxLayout()
        self.disclosure = QPushButton("Attachments", self)
        self.disclosure.setCheckable(True)
        self.disclosure.toggled.connect(self._expanded)
        self.disclosure.setObjectName("attachmentDisclosure")
        row.addWidget(self.disclosure)
        row.addStretch()
        self.upload = QPushButton("Upload files…", self)
        self.upload.clicked.connect(self.choose)
        self.upload.setVisible(project)
        row.addWidget(self.upload)
        self.project_files = QPushButton("Project files", self)
        self.project_files.clicked.connect(self.choose_project_files)
        self.project_files.hide()
        row.addWidget(self.project_files)
        self.visual_mode = QPushButton("Use images", self)
        self.visual_mode.setCheckable(True)
        self.visual_mode.setToolTip(
            "On: send up to 4 images to an Ollama vision model. "
            "Off: use extracted text only; charts and layout are not seen."
        )
        self.visual_mode.toggled.connect(self._set_visual_mode)
        self.visual_mode.hide()
        row.addWidget(self.visual_mode)
        self.sources = QPushButton("Sources used", self)
        self.sources.clicked.connect(self.show_sources)
        row.addWidget(self.sources)
        self.cancel = QPushButton("Cancel import", self)
        self.cancel.clicked.connect(self.cancel_import)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("settingsHint")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.files = QListWidget(self)
        self.files.setObjectName("attachmentList")
        self.files.setMaximumHeight(120)
        self.files.itemDoubleClicked.connect(lambda item: self.preview())
        self.files.currentItemChanged.connect(self._selection)
        layout.addWidget(self.files)
        self.actions = QWidget(self)
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.open = QPushButton("Read file" if project else "Read attachment", self)
        self.open.clicked.connect(self.preview)
        actions.addWidget(self.open)
        self.replace = QPushButton("Replace…", self)
        self.replace.clicked.connect(self.replace_selected)
        self.replace.setVisible(project)
        actions.addWidget(self.replace)
        self.remove = QPushButton("Remove", self)
        self.remove.clicked.connect(self.remove_selected)
        actions.addWidget(self.remove)
        actions.addStretch()
        layout.addWidget(self.actions)
        self.hide()

    def set_conversation(self, cid):
        if cid != self.cid and self._file_selector is not None:
            self._file_selector.close()
        if self.cid is not None and self.cid < 0 and cid != self.cid:
            self.cancel_import()
            for dialog in list(self._dialogs):
                dialog.close()
            self._dialogs.clear()
            self._errors.pop(self.cid, None)
        self.cid = cid
        self.refresh()
        self.busy_changed.emit()

    def busy(self):
        return self.thread is not None and self.thread.cid == self.cid

    def choose(self):
        if self.service is None or self.cid is None or self._generation:
            return
        title = "Upload project files" if self.project_scope else "Attach documents"
        paths, _ = QFileDialog.getOpenFileNames(self, title, "", FILTER)
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        importer = self.service.import_project_file if self.project_scope and self.service else None
        self._start_import(paths, importer=importer)

    def _start_import(self, paths, *, importer=None):
        if self.service is None or self.cid is None or self._generation:
            return
        if self.thread is not None:
            self._errors[self.cid] = "Wait for the current import to finish, or cancel it."
            self.refresh()
            return
        pages = None
        if any(Path(path).suffix.lower() == ".pdf" for path in paths):
            while True:
                value, accepted = QInputDialog.getText(
                    self,
                    "PDF pages for visual reading",
                    "Choose up to 8 pages for pictures and OCR, for example 1-3, 7.\n"
                    "Leave blank for the first 8 pages. Text extraction covers up to 200 pages.",
                )
                if not accepted:
                    return
                try:
                    pages = page_selection(value)
                    break
                except ValueError as exc:
                    QMessageBox.information(self, "Choose PDF pages", str(exc))
        self._errors.pop(self.cid, None)
        thread = ImportThread(
            self.service, self.cid, list(paths), self, importer=importer, pages=pages
        )
        self.thread = thread
        thread.progress.connect(self._progress)
        thread.finished.connect(self._finished)
        self.disclosure.setChecked(True)
        self.refresh()
        self.busy_changed.emit()
        thread.start()

    def _progress(self, text):
        if self.busy():
            self.status.setText(text)

    def _finished(self):
        thread = self.thread
        if thread is None:
            return
        if thread.errors and (thread.cid >= 0 or thread.cid == self.cid):
            self._errors[thread.cid] = "\n".join(thread.errors)
        elif thread.cancel.is_set() and thread.cid == self.cid:
            self._errors[thread.cid] = "Import cancelled. Completed attachments are kept."
        self.thread = None
        thread.deleteLater()
        self.refresh()
        self.busy_changed.emit()
        self.files_changed.emit()

    def cancel_import(self):
        if self.thread is not None:
            self.thread.cancel.set()
            self.cancel.setEnabled(False)
            self.status.setText("Cancelling import…")

    def shutdown(self):
        self.cancel_import()
        if self.thread is not None and not self.thread.wait(3000):
            return False
        # Drain completion while the database is still open; a queued finished signal
        # can otherwise arrive after the window's owner has closed its store.
        if self.thread is not None:
            self._finished()
        for dialog in list(self._dialogs):
            dialog.close()
        self._dialogs.clear()
        return True

    def set_generating(self, generating):
        self._generation = generating
        if self._file_selector is not None:
            self._file_selector.setEnabled(not generating)
        self._selection()
        self.visual_mode.setEnabled(not generating and not self.busy())

    def _set_visual_mode(self, enabled):
        if self.service is not None and self.cid is not None and not self.project_scope:
            self.service.set_use_images(self.cid, enabled)
            self.refresh()

    def has_documents(self):
        return self._has_documents

    def refresh(self):
        selected = self._selected()
        self.files.clear()
        docs = (
            (
                self.service.list_project(self.cid)
                if self.project_scope
                else self.service.list(self.cid)
            )
            if self.service is not None and self.cid is not None
            else []
        )
        for doc in docs:
            state = (
                f"Version {doc.version}"
                if self.project_scope
                else ("In chat" if doc.sent else "Ready to send")
            )
            item = QListWidgetItem(f"{doc.name}  ·  {state}")
            item.setData(Qt.ItemDataRole.UserRole, doc)
            item.setToolTip(doc.warning or "Text extracted and ready for questions")
            self.files.addItem(item)
        if docs:
            index = next((i for i, doc in enumerate(docs) if selected and doc.id == selected.id), 0)
            self.files.setCurrentRow(index)
        self.files.setFixedHeight(min(120, max(36, len(docs) * 32 + 4)))
        self.disclosure.setText(f"{'Files' if self.project_scope else 'Attachments'} · {len(docs)}")
        self.cancel.setVisible(self.busy())
        self.cancel.setEnabled(self.busy() and not self.thread.cancel.is_set())
        project_docs = []
        has_sources = False
        if self.service is not None and self.cid is not None and not self.project_scope:
            project_docs = self.service.project_files_for_chat(self.cid)
            has_sources = bool(self.service.sources(self.cid))
        if self._file_selector is not None and self._selector_versions != tuple(
            (doc.id, doc.version) for doc, _ in project_docs
        ):
            self._file_selector.close()
        self.project_files.setVisible(bool(project_docs))
        visual_count = sum(len(doc.images) for doc in docs) + sum(
            len(doc.images) for doc, enabled in project_docs if enabled
        )
        self.visual_mode.setVisible(not self.project_scope and visual_count > 0)
        self.visual_mode.blockSignals(True)
        enabled = bool(
            self.service
            and self.cid is not None
            and not self.project_scope
            and self.service.use_images(self.cid)
        )
        self.visual_mode.setChecked(enabled)
        self.visual_mode.setText("Use images · on" if enabled else "Use images · off")
        self.visual_mode.blockSignals(False)
        self.visual_mode.setEnabled(not self._generation and not self.busy())
        self._has_documents = bool(docs) or any(enabled for _, enabled in project_docs)
        self.project_files.setText(
            f"Project files · {sum(enabled for _, enabled in project_docs)}/{len(project_docs)}"
        )
        self.sources.setVisible(not self.project_scope and has_sources)
        self.status.setText(
            "Reading documents…"
            if self.busy()
            else self._errors.get(self.cid)
            or (
                "Shared with this project's chats. Choose files for each chat beside its prompt. "
                "Replacing or removing a file affects future replies; earlier sources are kept."
                if self.project_scope
                else "Choose project files. Replies use up to 8,000 characters of source data."
                if project_docs and not docs
                else "Earlier document sources are saved with the chat."
                if has_sources and not docs
                else (
                    "Selected excerpts are used in replies. Double-click a file to read it."
                    " Reply context is limited to 8,000 characters of source data."
                    + (
                        " Some files have reading limitations; open them for details."
                        if any(doc.warning for doc in docs)
                        else ""
                    )
                    if docs
                    else ""
                )
            )
        )
        if visual_count and not self.busy() and self.cid not in self._errors:
            note = f" {visual_count} image/page previews. Replies can use up to 4 images. " + (
                "Choose an Ollama model marked Vision."
                if enabled
                else "Extracted text only; images and charts are not seen."
            )
            if self.project_scope:
                note = f" {visual_count} image/page previews available to this project's chats."
            self.status.setText(self.status.text() + note)
        self.setVisible(
            bool(
                self.project_scope
                or project_docs
                or has_sources
                or docs
                or self.busy()
                or self._errors.get(self.cid)
            )
        )
        self.disclosure.setVisible(bool(docs) or self.project_scope or self.busy())
        self._expanded(self.disclosure.isChecked())
        self._selection()

    def _expanded(self, expanded):
        self.files.setVisible(expanded and self.files.count() > 0)
        self.actions.setVisible(expanded and self.files.count() > 0)

    def _selected(self):
        item = self.files.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _selection(self, *_args):
        doc = self._selected()
        self.remove.setEnabled(
            bool(
                doc
                and (self.project_scope or not doc.sent)
                and not self._generation
                and not self.busy()
            )
        )
        self.open.setEnabled(doc is not None)
        self.replace.setEnabled(bool(doc and not self._generation and not self.busy()))
        self.upload.setEnabled(self.cid is not None and not self._generation and not self.busy())
        self.project_files.setEnabled(not self._generation)

    def remove_selected(self):
        doc = self._selected()
        if doc and not self._generation and not self.busy():
            if self.project_scope:
                answer = QMessageBox.question(
                    self,
                    "Remove project file",
                    f"Remove “{doc.name}” from this project? "
                    "Future replies will stop retrieving it. Earlier source excerpts are kept.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            try:
                if self.project_scope:
                    self.service.remove_project_file(self.cid, doc)
                else:
                    self.service.remove_draft(self.cid, doc.id)
            except EngineError as exc:
                self._errors[self.cid] = str(exc)
            self.refresh()
            self.files_changed.emit()

    def replace_selected(self):
        doc = self._selected()
        if not self.project_scope or doc is None or self._generation or self.busy():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Replace project file", "", FILTER)
        if path:
            self._start_import(
                [path],
                importer=lambda pid, path, cancel, **options: self.service.import_project_file(
                    pid, path, cancel, replacing=doc, **options
                ),
            )

    def _reader(self, title, text):
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(title)
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit(dialog)
        content.setReadOnly(True)
        content.setPlainText(text)
        layout.addWidget(content)
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda: self._dialogs.remove(dialog) if dialog in self._dialogs else None
        )
        dialog.show()
        return dialog, layout

    def preview(self):
        doc = self._selected()
        if doc is None:
            return
        dialog, layout = self._reader(
            doc.name,
            "\n\n".join(
                [doc.warning] + [f"[{location}]\n{text}" for location, text in doc.segments]
            ),
        )
        if doc.images:
            selector = QComboBox(dialog)
            for page in doc.images:
                selector.addItem(page.location, page.index)
            picture = QLabel(dialog)
            picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
            picture.setFixedHeight(260)
            cid = self.cid

            def show_image():
                try:
                    data = self.service.visual_data(cid, doc, selector.currentData())
                    pixmap = QPixmap()
                    if not pixmap.loadFromData(data):
                        raise EngineError("document_failed", "Could not display this image.")
                    picture.setPixmap(
                        pixmap.scaled(
                            680,
                            260,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                except EngineError as exc:
                    picture.setText(str(exc))

            selector.currentIndexChanged.connect(show_image)
            layout.insertWidget(0, selector)
            layout.insertWidget(1, picture)
            show_image()
        save = QPushButton("Save original…", dialog)
        cid = self.cid
        save.clicked.connect(lambda: self._save_original(cid, doc))
        layout.addWidget(save)

    def _save_original(self, cid, doc):
        path, _ = QFileDialog.getSaveFileName(self, "Save original attachment", doc.name)
        if path:
            try:
                data = (
                    self.service.project_original(cid, doc)
                    if self.project_scope
                    else self.service.original(cid, doc.id)
                )
                Path(path).write_bytes(data)
            except (OSError, EngineError) as exc:
                QMessageBox.warning(self, "Could not save attachment", str(exc))

    def show_sources(self):
        history = self.service.source_history(self.cid)
        sections = []
        for question, sources in history:
            excerpts = "\n\n".join(
                f"[{item['source']}] {item['name']} · {item['location']}"
                + (f" · Project file version {item['version']}" if "version" in item else "")
                + f"\n{item['text']}"
                + (f"\nReading limitation: {item['warning']}" if item.get("warning") else "")
                for item in sources
            )
            if excerpts:
                sections.append(f"Question: {question}\n\n{excerpts}")
        text = "\n\n────────────────────\n\n".join(sections)
        self._reader("Document excerpts supplied to replies", text or "No excerpts used yet.")

    def choose_project_files(self):
        if self.service is None or self.cid is None or self.cid < 0 or self._generation:
            return
        if self._file_selector is not None:
            self._file_selector.raise_()
            return
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle("Project files for this chat")
        dialog.resize(520, 360)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "Checked files are available to future replies in this chat. "
            "New project uploads start checked. Earlier replies keep their sources.",
            dialog,
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        choices = QWidget(scroll)
        choices_layout = QVBoxLayout(choices)
        scroll.setWidget(choices)
        layout.addWidget(scroll, 1)
        cid = self.cid
        project_docs = self.service.project_files_for_chat(cid)
        self._selector_versions = tuple((doc.id, doc.version) for doc, _ in project_docs)
        for doc, enabled in project_docs:
            check = QCheckBox(f"{doc.name} · Version {doc.version}", dialog)
            check.setChecked(enabled)
            check.setToolTip(doc.warning or "Available for passage retrieval")
            check.toggled.connect(
                lambda checked, key=doc.id: self._set_project_file(cid, key, checked)
            )
            choices_layout.addWidget(check)
        choices_layout.addStretch()
        close = QPushButton("Done", dialog)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        self._file_selector = dialog
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda: self._close_selector(dialog))
        dialog.show()

    def _close_selector(self, dialog):
        self._file_selector = None
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)

    def _set_project_file(self, cid, key, enabled):
        try:
            self.service.set_project_file_enabled(cid, key, enabled)
        except EngineError as exc:
            self._errors[cid] = str(exc)
            if self._file_selector is not None:
                self._file_selector.close()
        self.refresh()
