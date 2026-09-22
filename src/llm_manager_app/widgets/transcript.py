"""Transcript: plain text + caret while streaming; markdown only after done."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QPainter,
    QPaintEvent,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QFrame,
    QPlainTextEdit,
    QStackedWidget,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import ChatTurn
from llm_manager_app.tokens import current_palette, mono_font_family, system_font_family

_CARET_W = 2
_CARET_H = 14


_PRE = re.compile(r"<pre>(.*?)</pre>", re.DOTALL)


def split_thinking(text: str, *, streaming: bool = False) -> tuple[str, str, bool]:
    """Separate an explicit leading reasoning block without changing the stored response."""
    stripped = text.lstrip()
    tags = ("<think>", "<thinking>", "<analysis>")
    if streaming and stripped and any(tag.startswith(stripped.lower()) for tag in tags):
        return "", "", True
    match = re.match(r"<(think|thinking|analysis)>", stripped, re.I)
    if match is None:
        return "", text, False
    body = stripped[match.end():]
    closing = f"</{match[1].lower()}>"
    end = body.lower().find(closing)
    if end < 0:
        for size in range(len(closing) - 1, 0, -1):
            if body.lower().endswith(closing[:size]):
                body = body[:-size]
                break
        return body.strip(), "", streaming
    return body[:end].strip(), body[end + len(closing):].lstrip(), False


def _flatten_pre(rendered: str) -> str:
    # Qt paints each pre line as its own background; one paragraph keeps a single block.
    def replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        inner = re.sub(r"^<code[^>]*>", "", inner)
        inner = re.sub(r"</code>\s*$", "", inner)
        inner = inner.strip("\n").replace("\n", "<br>\n")
        return f'<p class="code">{inner}</p>'

    return _PRE.sub(replace, rendered)


def _markdown(text: str) -> str:
    try:
        import markdown
    except ImportError:
        return "<p>" + html.escape(text).replace("\n", "<br>\n") + "</p>"
    return _flatten_pre(markdown.markdown(text, extensions=["fenced_code", "nl2br", "sane_lists"]))


def prefers_reduced_motion() -> bool:
    app = QGuiApplication.instance()
    if app is None:
        return False
    return app.styleHints().cursorFlashTime() <= 0


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
        color = QColor(current_palette().accent)
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
        self._assistant_label = "Assistant"
        self._expanded_thoughts: set[int] = set()
        self._reasoning_active = False

        self._browser = QTextBrowser(self)
        self._browser.setObjectName("transcriptHistory")
        self._browser.setOpenExternalLinks(True)
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._open_link)
        self._browser.setFrameShape(QFrame.Shape.NoFrame)
        self._browser.document().setDocumentMargin(12)

        self._plain = QPlainTextEdit(self)
        self._plain.setObjectName("transcriptStream")
        self._plain.setReadOnly(True)
        self._plain.setUndoRedoEnabled(False)
        self._plain.setFrameShape(QFrame.Shape.NoFrame)
        self._plain.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._plain.document().setDocumentMargin(12)
        self._plain.updateRequest.connect(self._on_plain_update)

        self._caret = StreamCaret(self._plain.viewport())
        self._plain.viewport().installEventFilter(self)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._browser)
        self._stack.addWidget(self._plain)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self._thought_toggle = QToolButton(self)
        self._thought_toggle.setObjectName("thinkingDisclosure")
        self._thought_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._thought_toggle.setCheckable(True)
        self._thought_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._thought_toggle.toggled.connect(self._toggle_thinking)
        self._thought_toggle.hide()
        layout.addWidget(self._thought_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._thought_text = QPlainTextEdit(self)
        self._thought_text.setObjectName("thinkingBody")
        self._thought_text.setReadOnly(True)
        self._thought_text.setAccessibleName("Model thinking")
        self._thought_text.setMaximumHeight(150)
        self._thought_text.hide()
        layout.addWidget(self._thought_text)

        self._render_html()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._plain.viewport() and event.type() == QEvent.Type.Resize:
            self._place_caret()
        return super().eventFilter(watched, event)

    def turns(self) -> tuple[ChatTurn, ...]:
        return tuple(self._turns)

    def buffer(self) -> str:
        return self._buffer

    def is_streaming(self) -> bool:
        return self._streaming

    def is_plain(self) -> bool:
        return self._stack.currentWidget() is self._plain

    def set_assistant_label(self, label: str) -> None:
        label = label.strip() or "Assistant"
        if label != self._assistant_label:
            self._assistant_label = label
            if self.is_plain():
                scroll = self._plain.verticalScrollBar().value()
                self._plain.setPlainText(self._dump_plain(with_buffer=self._streaming))
                if self._streaming:
                    cursor = self._plain.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    self._plain.setTextCursor(cursor)
                self._plain.verticalScrollBar().setValue(scroll)
                self._place_caret()
            else:
                self._render_html(preserve_scroll=True)

    def refresh_theme(self) -> None:
        if self.is_plain():
            return
        self._render_html()

    def set_turns(self, turns: Sequence[ChatTurn]) -> None:
        self._turns = list(turns)
        self._buffer = ""
        self._streaming = False
        self._plain_locked = False
        self._caret.stop()
        self._expanded_thoughts.clear()
        self._thought_toggle.setChecked(False)
        self._sync_thinking()
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
        self._sync_thinking()
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
        self._thought_toggle.setChecked(False)
        self._sync_thinking()
        self._plain.setPlainText(self._dump_plain(with_buffer=True))
        self._stack.setCurrentWidget(self._plain)
        self._place_caret()
        self._caret.start()
        self._plain.ensureCursorVisible()

    def restore_stream(self, buffer: str) -> None:
        self._buffer = buffer
        self._streaming = True
        self._plain_locked = False
        self._sync_thinking()
        self._plain.setPlainText(self._dump_plain(with_buffer=True))
        self._stack.setCurrentWidget(self._plain)
        self._place_caret()
        self._caret.start()
        self._plain.ensureCursorVisible()

    def append_stream(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        self._sync_thinking()
        visible = self._dump_plain(with_buffer=True)
        if visible != self._plain.toPlainText():
            self._plain.setPlainText(visible)
            cursor = self._plain.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._plain.setTextCursor(cursor)
        self._place_caret()
        self._plain.ensureCursorVisible()

    def finish_stream(self, *, parse_markdown: bool) -> None:
        if self._buffer:
            self._turns.append(ChatTurn(role="assistant", content=self._buffer))
        self._buffer = ""
        self._streaming = False
        self._caret.stop()
        self._sync_thinking()
        if parse_markdown or any(
            turn.role == "assistant" and split_thinking(turn.content)[0] for turn in self._turns
        ):
            self._plain_locked = False
            self._render_html()
            self._stack.setCurrentWidget(self._browser)
            return
        self._plain_locked = True
        self._plain.setPlainText(self._dump_plain(with_buffer=False))
        self._stack.setCurrentWidget(self._plain)

    def keep_stream(self) -> None:
        self.finish_stream(parse_markdown=False)

    def _role_label(self, role: str) -> str:
        if role == "user":
            return "You"
        if role == "assistant":
            return self._assistant_label
        return role.title()

    def _dump_plain(self, *, with_buffer: bool) -> str:
        blocks = []
        for turn in self._turns:
            thinking, answer, _ = split_thinking(turn.content) if turn.role == "assistant" else (
                "", turn.content, False,
            )
            content = ("[Thinking collapsed]\n" if thinking else "") + answer
            blocks.append(f"{self._role_label(turn.role)}\n{content}")
        if with_buffer:
            _, answer, _ = split_thinking(self._buffer, streaming=self._streaming)
            blocks.append(f"{self._assistant_label}\n{answer}")
        return "\n\n".join(blocks)

    def _sync_thinking(self) -> None:
        thinking, _, active = split_thinking(self._buffer, streaming=self._streaming)
        self._reasoning_active = active
        visible = self._streaming and (bool(thinking) or active)
        self._thought_toggle.setVisible(visible)
        self._thought_toggle.setEnabled(bool(thinking))
        self._thought_toggle.setText("Thinking…" if active else "Thinking complete")
        self._thought_text.setVisible(visible and self._thought_toggle.isChecked())
        if self._thought_toggle.isChecked():
            self._thought_text.setPlainText(thinking)

    def _toggle_thinking(self, expanded: bool) -> None:
        self._thought_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._sync_thinking()

    def _open_link(self, url) -> None:
        fragment = url.fragment()
        if re.fullmatch(r"thinking-\d+", fragment) and not url.scheme():
            index = int(fragment.split("-")[1])
            if index in self._expanded_thoughts:
                self._expanded_thoughts.remove(index)
            else:
                self._expanded_thoughts.add(index)
            self._render_html(preserve_scroll=True)
        elif url.scheme() in {"https", "http", "mailto"}:
            QDesktopServices.openUrl(url)

    def _on_plain_update(self, _rect: QRect, _dy: int) -> None:
        self._place_caret()

    def _place_caret(self) -> None:
        if not self._streaming or self._reasoning_active:
            self._caret.hide()
            return
        cursor = self._plain.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        rect = self._plain.cursorRect(cursor)
        y = rect.y() + max(0, (rect.height() - _CARET_H) // 2)
        x = rect.x() + 1
        caret_rect = QRect(x, y, _CARET_W, _CARET_H)
        if not self._plain.viewport().rect().intersects(caret_rect):
            self._caret.hide()
            return
        self._caret.move(x, y)
        self._caret.show()
        self._caret.raise_()

    def _scroll_browser_to_end(self) -> None:
        cursor = self._browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._browser.setTextCursor(cursor)
        self._browser.ensureCursorVisible()
        bar = self._browser.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _render_html(self, *, preserve_scroll: bool = False) -> None:
        scroll = self._browser.verticalScrollBar().value()
        palette = current_palette()
        family = system_font_family()
        mono = mono_font_family()
        parts = [
            "<html><head><style>",
            f"body {{ color: {palette.text}; background-color: {palette.canvas}; "
            f'font-family: "{family}"; font-size: 16px; line-height: 1.6; }}',
            f'code, p.code {{ font-family: "{mono}"; background-color: {palette.elevated}; }}',
            "p.code { padding: 10px 12px; margin: 8px 0; }",
            "p.code code { background-color: transparent; }",
            "p { margin: 0 0 14px 0; line-height: 150%; }",
            "h1, h2, h3 { margin: 22px 0 12px 0; font-size: 17px; }",
            f"a {{ color: {palette.accent}; }}",
            f".role {{ color: {palette.secondary}; font-size: 12px; font-weight: 600; "
            "margin: 24px 0 10px 0; }",
            ".turn { margin: 0 0 20px 0; }",
            "</style></head><body>",
        ]
        for index, turn in enumerate(self._turns):
            role_class = html.escape(turn.role)
            label = html.escape(self._role_label(turn.role))
            if turn.role == "assistant":
                thinking, answer, _ = split_thinking(turn.content)
                body = _markdown(answer)
                if thinking:
                    expanded = index in self._expanded_thoughts
                    label_text = "Hide thinking" if expanded else "Show thinking"
                    disclosure = f'<p><a href="#thinking-{index}">{label_text}</a></p>'
                    detail = (
                        "<p>" + html.escape(thinking).replace("\n", "<br>") + "</p>"
                        if expanded else ""
                    )
                    body = disclosure + detail + body
            else:
                body = "<p>" + html.escape(turn.content).replace("\n", "<br>\n") + "</p>"
            parts.append(
                f'<div class="turn {role_class}"><p class="role">{label}</p>{body}</div>'
            )
        parts.append("</body></html>")
        self._browser.setHtml("".join(parts))
        if preserve_scroll:
            self._browser.verticalScrollBar().setValue(scroll)
        else:
            self._scroll_browser_to_end()
            QTimer.singleShot(0, self._scroll_browser_to_end)
