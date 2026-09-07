"""Composer: Return sends, Shift+Return inserts a newline, height 40–140px."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QResizeEvent, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

_MIN_H = 40
_MAX_H = 140
_SEND_PX = 32


class ComposerEdit(QPlainTextEdit):
    send_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._return_sends = True

    def set_return_sends(self, enabled: bool) -> None:
        self._return_sends = enabled

    def return_sends(self) -> bool:
        return self._return_sends

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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._generating = False
        self._blocked = False
        self._send_allowed = True

        self._edit = ComposerEdit(self)
        self._edit.setObjectName("composerEdit")
        self._edit.setPlaceholderText("Ask anything, or work through an idea…")
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
        self._send.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._send.setAccessibleName("Send message")
        self._send.clicked.connect(self._on_send_clicked)

        self._hint = QLabel("Enter to send · Shift + Enter for a new line", self)
        self._hint.setObjectName("composerHint")
        footer = QHBoxLayout()
        footer.setContentsMargins(8, 0, 4, 0)
        footer.addWidget(self._hint, 1)
        footer.addWidget(self._send)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        layout.addWidget(self._edit)
        layout.addLayout(footer)

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
        self._hint.setText(
            "Enter to send · Shift + Enter for a new line"
            if enabled else "⌘ / Ctrl + Enter to send · Enter for a new line"
        )

    def return_sends(self) -> bool:
        return self._edit.return_sends()

    def submit(self) -> None:
        if self._generating or self._blocked:
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
        self._edit.setEnabled(enabled and not self._generating and not self._blocked)
        if self._generating:
            self._send.setText("■")
            self._send.setToolTip("Stop")
            self._send.setAccessibleName("Stop generation")
            self._send.setProperty("mode", "stop")
            self._send.setEnabled(enabled)
        else:
            self._send.setText("↑")
            self._send.setToolTip("Send")
            self._send.setAccessibleName("Send message")
            self._send.setProperty("mode", "send")
            self._send.setEnabled(enabled and self._send_allowed and not self._blocked)
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
