"""Composer: Return sends, Shift+Return inserts a newline, height 40–140px."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QPushButton, QWidget

_MIN_H = 40
_MAX_H = 140
_SEND_PX = 28


class ComposerEdit(QPlainTextEdit):
    send_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                event.accept()
                self.send_requested.emit()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    send_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")

        self._edit = ComposerEdit(self)
        self._edit.setObjectName("composerEdit")
        self._edit.setPlaceholderText("Message")
        self._edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._edit.setTabChangesFocus(True)
        self._edit.setFixedHeight(_MIN_H)
        self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._edit.send_requested.connect(self.submit)
        self._edit.textChanged.connect(self._fit_height)

        self._send = QPushButton("↑", self)
        self._send.setObjectName("sendButton")
        self._send.setToolTip("Send")
        self._send.setFixedSize(_SEND_PX, _SEND_PX)
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setDefault(False)
        self._send.setAutoDefault(False)
        self._send.clicked.connect(self.submit)
        self._send_allowed = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._edit, 1)
        layout.addWidget(self._send, 0, Qt.AlignmentFlag.AlignBottom)

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

    def submit(self) -> None:
        if not self.isEnabled() or not self._send.isEnabled():
            return
        text = self._edit.toPlainText()
        if not text.strip():
            return
        self.send_requested.emit(text)

    def set_send_enabled(self, enabled: bool) -> None:
        self._send_allowed = enabled
        self._send.setEnabled(self.isEnabled() and enabled)

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self._edit.setEnabled(enabled)
        self._send.setEnabled(enabled and self._send_allowed)

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
