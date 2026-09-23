"""Composer: Return sends, Shift+Return inserts a newline, height 40–140px."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from llm_manager_app.icons import icon

_MIN_H = 40
_MAX_H = 140
_SEND_PX = 34


class ComposerEdit(QPlainTextEdit):
    send_requested = Signal()
    files_dropped = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._return_sends = True

    def _center_text(self):
        # Use the actual font metrics, including display scaling, instead of
        # fixed stylesheet padding. Keep the same inset when the editor grows.
        metrics = self.fontMetrics()
        # Qt needs a little space below the line for the cursor; an exact
        # line-height viewport shows a scrollbar even when the field is empty.
        line_height = max(metrics.height(), metrics.lineSpacing())
        inset = max(0, (_MIN_H - line_height - 2 * self.frameWidth() - 2) // 2)
        if self.viewportMargins().top() != inset:
            self.setViewportMargins(0, inset, 0, inset)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._center_text()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._center_text()

    def set_return_sends(self, enabled: bool) -> None:
        self._return_sends = enabled

    def return_sends(self) -> bool:
        return self._return_sends

    def canInsertFromMimeData(self, source):
        return source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasUrls() and all(url.isLocalFile() for url in source.urls()):
            self.files_dropped.emit([url.toLocalFile() for url in source.urls()])
        else:
            super().insertFromMimeData(source)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            super().keyPressEvent(event)
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            super().keyPressEvent(event)
            return
        ctrlish = bool(
            event.modifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        )
        if self._return_sends or ctrlish:
            event.accept()
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    send_requested = Signal(str)
    stop_requested = Signal()
    attach_requested = Signal()
    files_dropped = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._generating = False
        self._blocked = False
        self._send_allowed = True
        self._importing = False

        self._edit = ComposerEdit(self)
        self._edit.setObjectName("composerEdit")
        self._edit.setPlaceholderText("Message your model")
        self._edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._edit.setTabChangesFocus(True)
        self._edit.document().setDocumentMargin(0)
        self._edit.setFixedHeight(_MIN_H)
        self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._edit.send_requested.connect(self.submit)
        self._edit.textChanged.connect(self._fit_height)
        self._edit.files_dropped.connect(self.files_dropped)

        self._attach = QPushButton(self)
        self._attach.setIcon(icon("plus"))
        self._attach.setIconSize(QSize(18, 18))
        self._attach.setObjectName("attachDocumentButton")
        self._attach.setFixedSize(28, 28)
        self._attach.setToolTip(
            "Attach documents or pictures (PDF, Word, Excel, text, PNG, JPEG, WebP)"
        )
        self._attach.setAccessibleName("Attach documents")
        self._attach.clicked.connect(self.attach_requested)
        self._attach.hide()

        self.create_files = QPushButton("Create files", self)
        self.create_files.setObjectName("createFilesButton")
        self.create_files.setCheckable(True)
        self.create_files.setToolTip(
            "Create documents, spreadsheets, slides, forms, reports, data files and diagrams. "
            "Turn off for a normal chat reply."
        )
        self.create_files.setAccessibleName("Create files for this reply")
        self.create_files.toggled.connect(lambda checked: self.create_files.setText(
            "Create files · On" if checked else "Create files"
        ))

        self.web_search = QPushButton("Web search", self)
        self.web_search.setObjectName("webSearchButton")
        self.web_search.setCheckable(True)
        self.web_search.setAccessibleName("Web search for this reply")
        self.web_search.setToolTip(
            "Off: no web requests. On: send up to 500 characters of this question to a "
            "search service (Exa's free search, or a service you added a key for) and read "
            "the results. That service may keep the question under its own privacy policy. "
            "Files and saved conversations are not sent. Also applies to Retry."
        )
        self.web_search.toggled.connect(lambda checked: self.web_search.setText(
            "Web search · On" if checked else "Web search"
        ))

        self._send = QPushButton(self)
        self._send.setIcon(icon("send"))
        self._send.setIconSize(QSize(20, 20))
        self._send.setObjectName("sendButton")
        self._send.setToolTip("Send")
        self._send.setFixedSize(_SEND_PX, _SEND_PX)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setDefault(False)
        self._send.setAutoDefault(False)
        self._send.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._send.setAccessibleName("Send message")
        self._send.clicked.connect(self._on_send_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 6, 10, 6)
        layout.setSpacing(6)
        entry = QHBoxLayout()
        entry.setSpacing(10)
        entry.addWidget(self._attach, 0, Qt.AlignmentFlag.AlignVCenter)
        entry.addWidget(self._edit, 1)
        entry.addWidget(self._send, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(entry)
        self.controls = QHBoxLayout()
        self.controls.setSpacing(8)
        self.controls.addWidget(self.create_files, 0, Qt.AlignmentFlag.AlignVCenter)
        self.controls.addWidget(self.web_search, 0, Qt.AlignmentFlag.AlignVCenter)
        self.controls.addStretch()
        layout.addLayout(self.controls)
        self.set_return_sends(True)

    def text(self) -> str:
        return self._edit.toPlainText()

    def set_text(self, text: str) -> None:
        self._edit.setPlainText(text)
        cursor = self._edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._edit.setTextCursor(cursor)
        self._fit_height()

    def clear(self) -> None:
        self._edit.clear()
        self._fit_height()

    def focus_edit(self) -> None:
        self._edit.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_return_sends(self, enabled: bool) -> None:
        self._edit.set_return_sends(enabled)
        self._edit.setToolTip(
            "Enter to send · Shift + Enter for a new line"
            if enabled
            else "⌘ / Ctrl + Enter to send · Enter for a new line"
        )

    def return_sends(self) -> bool:
        return self._edit.return_sends()

    def submit(self) -> None:
        if self._generating or self._blocked or self._importing:
            return
        if not self.isEnabled() or not self._send.isEnabled():
            return
        text = self._edit.toPlainText()
        if not text.strip():
            return
        self.send_requested.emit(text)

    def set_send_enabled(self, enabled: bool) -> None:
        self._send_allowed = enabled
        self._sync_controls()

    def set_importing(self, importing):
        self._importing = importing
        self._sync_controls()

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        self._sync_controls()

    def set_blocked(self, blocked: bool) -> None:
        self._blocked = blocked
        self._sync_controls()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._sync_controls()

    def _on_send_clicked(self) -> None:
        if self._generating:
            self.stop_requested.emit()
            return
        self.submit()

    def _sync_controls(self) -> None:
        enabled = self.isEnabled()
        self._attach.setEnabled(
            enabled and not self._generating and not self._blocked and not self._importing
        )
        self._edit.setEnabled(enabled and not self._generating and not self._blocked)
        self.create_files.setEnabled(enabled and not self._generating and not self._blocked)
        self.web_search.setEnabled(enabled and not self._generating and not self._blocked)
        if self._generating:
            self._send.setIcon(icon("stop-generation"))
            self._send.setToolTip("Stop")
            self._send.setAccessibleName("Stop generation")
            self._send.setProperty("mode", "stop")
            self._send.setEnabled(enabled)
        else:
            self._send.setIcon(icon("send"))
            self._send.setToolTip("Send")
            self._send.setAccessibleName("Send message")
            self._send.setProperty("mode", "send")
            self._send.setEnabled(
                enabled and self._send_allowed and not self._blocked and not self._importing
            )
        style = self._send.style()
        if style is not None:
            style.unpolish(self._send)
            style.polish(self._send)
        self._send.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def _visual_line_count(self) -> int:
        if not self._edit.toPlainText():
            return 1
        metrics = self._edit.fontMetrics()
        spacing = max(metrics.lineSpacing(), 1)
        width = self._edit.viewport().width()
        if width <= 0:
            width = max(self._edit.width(), 1)
        total = 0
        block = self._edit.document().firstBlock()
        while block.isValid():
            text = block.text()
            if not text:
                total += 1
            else:
                bound = metrics.boundingRect(
                    0,
                    0,
                    width,
                    10_000,
                    Qt.TextFlag.TextWordWrap,
                    text,
                )
                total += max(1, round(bound.height() / spacing))
            block = block.next()
        return max(1, total)

    def _fit_height(self) -> None:
        # QPlainTextDocumentLayout.size() ignores wrap; grow by visual lines.
        lines = self._visual_line_count()
        spacing = self._edit.fontMetrics().lineSpacing()
        height = _MIN_H + max(0, lines - 1) * spacing
        self._edit.setFixedHeight(max(_MIN_H, min(_MAX_H, height)))
