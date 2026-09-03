"""Transcript: plain text + caret while streaming; markdown only after done."""

from __future__ import annotations

import functools
import html
import subprocess
import sys
from collections.abc import Sequence

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QPlainTextEdit,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ChatTurn
from llm_manager_app.tokens import DARK, StudioPalette, palette_for_app, system_font_family

_CARET_W = 2
_CARET_H = 14


def _markdown(text: str) -> str:
    try:
        import markdown
    except ImportError:
        return "<p>" + html.escape(text).replace("\n", "<br>\n") + "</p>"
    return markdown.markdown(text, extensions=["fenced_code", "nl2br", "sane_lists"])


@functools.cache
def prefers_reduced_motion() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
            capture_output=True,
            text=True,
            timeout=0.3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.stdout.strip() == "1"


def _palette() -> StudioPalette:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return palette_for_app(app)
    return DARK


class StreamCaret(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("streamCaret")
        self.setFixedSize(_CARET_W, _CARET_H)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._bright = True
        self._timer = QTimer(self)
        self._timer.setInterval(530)
        self._timer.timeout.connect(self._pulse)
        self.hide()

    def start(self) -> None:
        self._bright = True
        self.show()
        self.raise_()
        if prefers_reduced_motion():
            self._timer.stop()
        else:
            self._timer.start()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _pulse(self) -> None:
        self._bright = not self._bright
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        color = QColor(_palette().accent)
        if not self._bright:
            color.setAlpha(90)
        painter.fillRect(self.rect(), color)


class Transcript(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("transcript")
        self._turns: list[ChatTurn] = []
        self._buffer = ""
        self._streaming = False
        self._plain_locked = False

        self._browser = QTextBrowser(self)
        self._browser.setObjectName("transcriptHistory")
        self._browser.setOpenExternalLinks(True)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)

        self._plain = QPlainTextEdit(self)
        self._plain.setObjectName("transcriptStream")
        self._plain.setReadOnly(True)
        self._plain.setUndoRedoEnabled(False)
        self._plain.setFrameShape(QFrame.Shape.NoFrame)
        self._plain.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

        self._caret = StreamCaret(self._plain.viewport())

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._browser)
        self._stack.addWidget(self._plain)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self._render_html()

    def turns(self) -> tuple[ChatTurn, ...]:
        return tuple(self._turns)

    def buffer(self) -> str:
        return self._buffer

    def is_streaming(self) -> bool:
        return self._streaming

    def is_plain(self) -> bool:
        return self._stack.currentWidget() is self._plain

    def set_turns(self, turns: Sequence[ChatTurn]) -> None:
        self._turns = list(turns)
        self._buffer = ""
        self._streaming = False
        self._plain_locked = False
        self._caret.stop()
        self._render_html()
        self._stack.setCurrentWidget(self._browser)

    def clear(self) -> None:
        self.set_turns(())

    def append_user(self, text: str) -> None:
        self._turns.append(ChatTurn(role="user", content=text))

    def drop_last_assistant(self) -> ChatTurn | None:
        if self._turns and self._turns[-1].role == "assistant":
            return self._turns.pop()
        return None

    def revert_stream(self, *, restore_user: bool = False, restore: ChatTurn | None = None) -> None:
        self._buffer = ""
        self._streaming = False
        self._plain_locked = False
        self._caret.stop()
        if restore_user and self._turns and self._turns[-1].role == "user":
            self._turns.pop()
        if restore is not None:
            self._turns.append(restore)
        self._render_html()
        self._stack.setCurrentWidget(self._browser)

    def begin_stream(self) -> None:
        self._buffer = ""
        self._streaming = True
        self._plain_locked = False
        self._plain.setPlainText(self._dump_plain(with_buffer=True))
        self._stack.setCurrentWidget(self._plain)
        self._place_caret()
        self._caret.start()
        self._plain.ensureCursorVisible()

    def restore_stream(self, buffer: str) -> None:
        self._buffer = buffer
        self._streaming = True
        self._plain_locked = False
        self._plain.setPlainText(self._dump_plain(with_buffer=True))
        self._stack.setCurrentWidget(self._plain)
        self._place_caret()
        self._caret.start()
        self._plain.ensureCursorVisible()

    def append_stream(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        cursor = self._plain.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._plain.setTextCursor(cursor)
        self._place_caret()
        self._plain.ensureCursorVisible()

    def finish_stream(self, *, parse_markdown: bool) -> None:
        if self._buffer:
            self._turns.append(ChatTurn(role="assistant", content=self._buffer))
        self._buffer = ""
        self._streaming = False
        self._caret.stop()
        if parse_markdown:
            self._plain_locked = False
            self._render_html()
            self._stack.setCurrentWidget(self._browser)
            return
        self._plain_locked = True
        self._plain.setPlainText(self._dump_plain(with_buffer=False))
        self._stack.setCurrentWidget(self._plain)

    def keep_stream(self) -> None:
        self.finish_stream(parse_markdown=False)

    def _dump_plain(self, *, with_buffer: bool) -> str:
        blocks = [turn.content for turn in self._turns]
        if with_buffer:
            blocks.append(self._buffer)
        return "\n\n".join(blocks)

    def _place_caret(self) -> None:
        cursor = self._plain.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        rect = self._plain.cursorRect(cursor)
        y = rect.y() + max(0, (rect.height() - _CARET_H) // 2)
        self._caret.move(rect.x() + 1, y)
        self._caret.show()
        self._caret.raise_()

    def _render_html(self) -> None:
        palette = _palette()
        family = system_font_family()
        parts = [
            "<html><head><style>",
            f"body {{ color: {palette.text}; background-color: {palette.canvas}; "
            f'font-family: "{family}"; }}',
            f"pre, code {{ background-color: {palette.elevated}; "
            f"border-radius: {palette.radius_control}px; }}",
            "pre { padding: 8px; }",
            f"a {{ color: {palette.accent}; }}",
            f".role {{ color: {palette.secondary}; }}",
            ".turn { margin: 0 0 16px 0; }",
            "</style></head><body>",
        ]
        for turn in self._turns:
            role = html.escape(turn.role)
            if turn.role == "assistant":
                body = _markdown(turn.content)
            else:
                body = "<p>" + html.escape(turn.content).replace("\n", "<br>\n") + "</p>"
            parts.append(
                f'<div class="turn {role}"><div class="role">{role}</div>{body}</div>'
            )
        parts.append("</body></html>")
        self._browser.setHtml("".join(parts))
