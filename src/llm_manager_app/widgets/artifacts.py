"""Generated-file cards, previews, explicit exports and template selection."""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from llm_engine.artifacts.generators import REGISTRY


class ArtifactPanel(QWidget):
    project_changed = Signal()

    def __init__(self, composer, parent=None):
        super().__init__(parent)
        self.composer = composer
        self.service = None
        self.library = None
        self.cid = None
        self.project_id = None
        self._requests = {}
        self._dialogs = []
        self.setObjectName("artifactPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        row = QHBoxLayout()
        self.disclosure = QPushButton("Generated files", self)
        self.disclosure.setCheckable(True)
        self.disclosure.toggled.connect(self.refresh)
        row.addWidget(self.disclosure)
        self.templates = QPushButton("File templates…", self)
        self.templates.clicked.connect(self._template_menu)
        row.addWidget(self.templates)
        formats = QPushButton("Formats", self)
        formats.clicked.connect(self._formats)
        row.addWidget(formats)
        self.bundle = QPushButton("Save batch ZIP…", self)
        self.bundle.clicked.connect(self._save_batch)
        row.addWidget(self.bundle)
        layout.addLayout(row)
        self.notice = QLabel(self)
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setMaximumHeight(180)
        layout.addWidget(self.scroll)
        composer.create_files.toggled.connect(self.refresh)
        self.hide()

    def set_conversation(self, cid, project_id=None):
        if self.cid is not None and self.cid < 0 and self.cid != cid:
            self._requests.pop(self.cid, None)
            for dialog in list(self._dialogs):
                dialog.close()
            self._dialogs.clear()
        self.cid, self.project_id = cid, project_id
        self.refresh()

    def request(self):
        if not self.composer.create_files.isChecked():
            return None
        return dict(self._requests.get(self.cid, {}))

    def refresh(self, *_args):
        items = self.service.list(self.cid) if self.service and self.cid is not None else []
        enabled = self.composer.create_files.isChecked()
        self.setVisible(bool(items) or enabled)
        self.disclosure.setText(f"Generated files ({len(items)})")
        self.templates.setVisible(self.cid is not None and self.cid >= 0)
        self.notice.setText(
            "Describe the files you need. For revisions, choose Revise on a file below."
            if enabled
            else "Earlier versions are retained. Save a file to open it in another app."
        )
        self.bundle.setEnabled(len(items) > 1)
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        content = QWidget(self.scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        for artifact in reversed(items):
            card = QFrame(content)
            card.setObjectName("artifactCard")
            card.setFrameShape(QFrame.Shape.StyledPanel)
            body = QVBoxLayout(card)
            label = QLabel(f"{artifact.name} · v{artifact.version}", card)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            body.addWidget(label)
            actions = QHBoxLayout()
            for title, callback in (
                ("Preview", self._preview),
                ("Save As…", self._save),
                ("Open…", self._open),
                ("Revise", self._revise),
                ("More…", self._more),
            ):
                button = QPushButton(title, card)
                button.setAccessibleName(f"{title} {artifact.name} version {artifact.version}")
                button.clicked.connect(lambda _checked=False, a=artifact, fn=callback: fn(a))
                actions.addWidget(button)
            body.addLayout(actions)
            layout.addWidget(card)
        layout.addStretch()
        self.scroll.setWidget(content)
        self.scroll.setVisible(bool(items) and self.disclosure.isChecked())

    def _error(self, exc):
        QMessageBox.warning(self, "Generated files", str(exc))

    def _formats(self):
        lines = ["Creation capabilities (independent of import support):", ""]
        for fmt, cap in REGISTRY.items():
            status = (
                "Available" if cap.available else "Missing: " + ", ".join(cap.missing_dependencies)
            )
            lines.append(f"{fmt.upper()}: {cap.features}\nPreview: {cap.preview}. {status}\n")
        lines.append(
            "Office previews show retained content; inspect native layout in Word, "
            "Excel or PowerPoint. PDF, PNG and SVG previews render the generated output. "
            "Revisions use files created here, not arbitrary imported Office layouts."
        )
        self._text_dialog("Supported formats", "\n".join(lines))

    def _text_dialog(self, title, text):
        dialog = QDialog(self)
        self._track_dialog(dialog)
        dialog.setWindowTitle(title)
        dialog.resize(700, 600)
        layout = QVBoxLayout(dialog)
        view = QPlainTextEdit(dialog)
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view)
        dialog.show()

    def _track_dialog(self, dialog):
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._dialogs.append(dialog)
        dialog.finished.connect(
            lambda _result: self._dialogs.remove(dialog) if dialog in self._dialogs else None
        )

    def _preview(self, artifact):
        try:
            item, sources = self.service.get(self.cid, artifact.id)
            dialog = QDialog(self)
            self._track_dialog(dialog)
            dialog.setWindowTitle(f"{artifact.name} · v{artifact.version}")
            dialog.resize(820, 720)
            layout = QVBoxLayout(dialog)
            warning = QLabel(item.warning, dialog)
            warning.setWordWrap(True)
            warning.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(warning)
            scroll = QScrollArea(dialog)
            scroll.setWidgetResizable(True)
            content = QWidget(scroll)
            pages = QVBoxLayout(content)
            for data in item.previews:
                pixmap = QPixmap()
                pixmap.loadFromData(data, "PNG")
                label = QLabel(content)
                label.setPixmap(
                    pixmap.scaledToWidth(730, Qt.TransformationMode.SmoothTransformation)
                )
                pages.addWidget(label)
            scroll.setWidget(content)
            layout.addWidget(scroll)
            source_text = "\n".join(
                f"{s.get('source', '')}: {s.get('name', '')} · {s.get('location', '')}"
                for s in sources
            )
            text_button = QPushButton("View retained content and sources", dialog)
            text_button.clicked.connect(
                lambda: self._text_dialog(
                    artifact.name, item.text + "\n\nSources supplied to the model:\n" + source_text
                )
            )
            layout.addWidget(text_button)
            dialog.show()
        except Exception as exc:
            self._error(exc)

    def _save_keys(self, keys, name):
        path, _ = QFileDialog.getSaveFileName(self, "Save generated file", name)
        if not path:
            return None
        # The native save dialog handles replacement confirmation.
        try:
            self.service.export(self.cid, keys, path, overwrite=Path(path).exists())
            return path
        except Exception as exc:
            self._error(exc)
            return None

    def _save(self, artifact):
        return self._save_keys([artifact.id], artifact.name)

    def _open(self, artifact):
        path = self._save(artifact)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _save_batch(self):
        items = self.service.list(self.cid)
        if items:
            batch = items[-1].batch_id
            keys = [a.id for a in items if a.batch_id == batch]
            if len(keys) < 2:
                self._error("The latest request created one file. Use its Save As action.")
            else:
                self._save_keys(keys, "deliverables.zip")

    def _revise(self, artifact):
        self._requests[self.cid] = {"revision": artifact.id}
        self.composer.create_files.setChecked(True)
        self.composer.set_text(f"Revise {artifact.name}: ")
        self.composer.focus_edit()

    def _more(self, artifact):
        menu = QMenu(self)
        add = menu.addAction("Add to project")
        add.setEnabled(self.cid >= 0 and self.project_id is not None)
        template = menu.addAction("Save as reusable template…")
        template.setEnabled(self.cid >= 0)
        chosen = menu.exec(self.cursor().pos())
        try:
            if chosen == add:
                self.service.add_to_project(self.cid, artifact.id, self.project_id)
                self.project_changed.emit()
                self.notice.setText(f"Added {artifact.name} to the project.")
            elif chosen == template:
                name, accepted = QInputDialog.getText(self, "File template", "Template name:")
                if accepted:
                    self.service.save_template(self.cid, [artifact.id], name)
                    self.notice.setText("Template saved, including this file's content.")
        except Exception as exc:
            self._error(exc)

    def _template_menu(self):
        if self.service is None or self.cid is None or self.cid < 0:
            return
        menu = QMenu(self)
        clear = menu.addAction("No template / revision")
        actions = {}
        for key, name in self.service.templates():
            submenu = menu.addMenu(name)
            actions[submenu.addAction("Use template")] = (key, name, False)
            actions[submenu.addAction("Delete template")] = (key, name, True)
        chosen = menu.exec(self.templates.mapToGlobal(self.templates.rect().bottomLeft()))
        if chosen == clear:
            self._requests.pop(self.cid, None)
            self.refresh()
        elif chosen in actions:
            key, name, deleting = actions[chosen]
            if deleting:
                if (
                    QMessageBox.question(self, "Delete template", f"Delete {name}?")
                    == QMessageBox.StandardButton.Yes
                ):
                    self.service.delete_template(key)
            else:
                self._requests[self.cid] = {"template": key}
                self.composer.create_files.setChecked(True)
                self.composer.set_text(f"Use the {name} template to create: ")
                self.composer.focus_edit()
