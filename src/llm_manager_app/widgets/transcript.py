"""Transcript: markdown rendered live while streaming (throttled); plain text kept after errors."""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPaintEvent,
    QTextBlockFormat,
    QTextCursor,
    QTextDocument,
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
from llm_manager_app.icons import icon
from llm_manager_app.motion import prefers_reduced_motion
from llm_manager_app.tokens import current_palette, mono_font_family, system_font_family

_CARET_W = 2
_CARET_H = 14
# Re-render streamed markdown at most this often; tokens in between are batched.
_STREAM_RENDER_MS = 80


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


def close_open_markup(text: str) -> str:
    """Close markdown left open mid-stream so '**bo' shows bold, not asterisks.

    Display only: the stored response is never changed.
    """
    if text.count("```") % 2:
        return text + "\n```"
    outside = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    if outside.count("`") % 2:
        return text + "`"
    if outside.count("**") % 2:
        text = text.rstrip() + "**"
    return text




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
    retry_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("transcript")
        # Retry sits under the last message: a link in rendered history, or a
        # button below the plain view kept after an error or unload.
        self._retry_label: str | None = None
        self._stream_start = 0
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

        self._caret = StreamCaret(self._browser.viewport())
        self._browser.viewport().installEventFilter(self)
        self._browser.verticalScrollBar().valueChanged.connect(self._place_caret)
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(_STREAM_RENDER_MS)
        self._stream_timer.timeout.connect(self._render_stream)

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
        # A thin chevron, as macOS disclosures use, rather than a solid triangle.
        self._thought_toggle.setIcon(icon("chevron-right"))
        self._thought_toggle.setIconSize(QSize(12, 12))
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
        self._retry_button = QToolButton(self)
        self._retry_button.setObjectName("retryResponseButton")
        self._retry_button.setIcon(icon("refresh"))
        self._retry_button.setIconSize(QSize(14, 14))
        self._retry_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._retry_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._retry_button.clicked.connect(self.retry_requested)
        self._retry_button.hide()
        layout.addWidget(self._retry_button, 0, Qt.AlignmentFlag.AlignLeft)

        self._render_html()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._browser.viewport() and event.type() == QEvent.Type.Resize:
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
                self._plain.setPlainText(self._dump_plain(with_buffer=False))
                self._plain.verticalScrollBar().setValue(scroll)
            else:
                self._render_html(preserve_scroll=True)
                self._place_caret()

    def set_retry(self, label: str | None) -> None:
        """Offer Retry under the last message with this label, or hide it (None)."""
        if label == self._retry_label:
            return
        self._retry_label = label
        self._sync_retry_button()
        if not self.is_plain():
            self._render_html(preserve_scroll=True)

    def retry_label(self) -> str | None:
        return self._retry_label

    def _sync_retry_button(self) -> None:
        label = self._retry_label
        self._retry_button.setText(label or "")
        self._retry_button.setAccessibleName(label or "")
        self._retry_button.setVisible(label is not None and self.is_plain() and not self._streaming)

    def refresh_theme(self) -> None:
        if self.is_plain():
            return
        self._render_html()

    def set_turns(self, turns: Sequence[ChatTurn]) -> None:
        self._turns = list(turns)
        self._buffer = ""
        self._streaming = False
        self._stream_timer.stop()
        self._plain_locked = False
        self._caret.stop()
        self._expanded_thoughts.clear()
        self._thought_toggle.setChecked(False)
        self._sync_thinking()
        self._render_html()
        self._stack.setCurrentWidget(self._browser)
        self._sync_retry_button()

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
        self._stream_timer.stop()
        self._plain_locked = False
        self._caret.stop()
        self._sync_thinking()
        if restore_user and self._turns and self._turns[-1].role == "user":
            self._turns.pop()
        if restore is not None:
            self._turns.append(restore)
        self._render_html()
        self._stack.setCurrentWidget(self._browser)
        self._sync_retry_button()

    def begin_stream(self) -> None:
        self._thought_toggle.setChecked(False)
        self.restore_stream("")

    def restore_stream(self, buffer: str) -> None:
        self._buffer = buffer
        self._streaming = True
        self._plain_locked = False
        self._sync_thinking()
        self._stream_timer.stop()
        self._render_html()
        self._stack.setCurrentWidget(self._browser)
        self._caret.start()
        self._place_caret()
        self._sync_retry_button()

    def append_stream(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        self._sync_thinking()
        # Batch tokens: rendering markdown per token would stall long answers.
        if not self._stream_timer.isActive():
            self._stream_timer.start()

    def flush_stream(self) -> None:
        """Render any tokens still waiting for the throttle timer."""
        if self._stream_timer.isActive():
            self._stream_timer.stop()
            self._render_stream()

    def _render_stream(self) -> None:
        if not self._streaming:
            return
        bar = self._browser.verticalScrollBar()
        # Follow the answer only while the reader is at the bottom.
        following = bar.value() >= bar.maximum() - 24
        scroll = bar.value()
        # Replace only the streaming turn: re-laying out the whole history every
        # tick cost ~60 ms in a 60-turn chat.
        cursor = QTextCursor(self._browser.document())
        cursor.setPosition(self._stream_start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        self._insert_stream(cursor)
        if following:
            self._scroll_browser_to_end()
        else:
            bar.setValue(scroll)
        self._place_caret()

    def _insert_stream(self, cursor: QTextCursor) -> None:
        cursor.insertHtml(self._stream_html())
        # The label merges into the anchor paragraph, which drops the .role
        # margins; restore them so live and finished turns line up.
        label = QTextCursor(self._browser.document())
        label.setPosition(self._stream_start)
        spacing = label.blockFormat()
        spacing.setTopMargin(24)
        spacing.setBottomMargin(10)
        spacing.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        label.setBlockFormat(spacing)

    def _stream_html(self) -> str:
        # Partial markdown renders as it arrives; thinking stays in the disclosure.
        _, answer, _ = split_thinking(self._buffer, streaming=True)
        answer = close_open_markup(answer)
        return (
            f'{self._html_head()}<p class="role">{html.escape(self._assistant_label)}</p>'
            f"{_markdown(answer)}</body></html>"
        )

    def finish_stream(self, *, parse_markdown: bool) -> None:
        self._stream_timer.stop()
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
            self._sync_retry_button()
            return
        self._plain_locked = True
        self._plain.setPlainText(self._dump_plain(with_buffer=False))
        self._stack.setCurrentWidget(self._plain)
        self._sync_retry_button()

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
        self._thought_toggle.setIcon(icon("chevron-down" if expanded else "chevron-right"))
        self._sync_thinking()

    def _open_link(self, url) -> None:
        fragment = url.fragment()
        if fragment == "retry" and not url.scheme():
            if self._retry_label is not None and not self._streaming:
                self.retry_requested.emit()
        elif re.fullmatch(r"thinking-\d+", fragment) and not url.scheme():
            index = int(fragment.split("-")[1])
            if index in self._expanded_thoughts:
                self._expanded_thoughts.remove(index)
            else:
                self._expanded_thoughts.add(index)
            self._render_html(preserve_scroll=True)
        elif url.scheme() in {"https", "http", "mailto"}:
            QDesktopServices.openUrl(url)

    def _place_caret(self, *_args) -> None:
        if not self._streaming or self._reasoning_active or self.is_plain():
            self._caret.hide()
            return
        cursor = QTextCursor(self._browser.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        rect = self._browser.cursorRect(cursor)
        y = rect.y() + max(0, (rect.height() - _CARET_H) // 2)
        x = rect.x() + 1
        caret_rect = QRect(x, y, _CARET_W, _CARET_H)
        if not self._browser.viewport().rect().intersects(caret_rect):
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

    def _html_head(self) -> str:
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
            f"a.action {{ color: {palette.secondary}; text-decoration: none; }}",
            "p.actions { margin: -6px 0 0 0; font-size: 13px; }",
            f".role {{ color: {palette.secondary}; font-size: 12px; font-weight: 600; "
            "margin: 24px 0 10px 0; }",
            ".turn { margin: 0 0 20px 0; }",
            "</style></head><body>",
        ]
        return "".join(parts)

    def _render_html(self, *, preserve_scroll: bool = False) -> None:
        scroll = self._browser.verticalScrollBar().value()
        parts = [self._html_head()]
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
        if self._streaming:
            # Anchor for the streaming turn, which _render_stream replaces in place.
            parts.append('<p class="role"></p>')
        retry = self._retry_label is not None and bool(self._turns) and not self._streaming
        if retry:
            parts.append(
                '<p class="actions"><a class="action" href="#retry">'
                '<img src="icon:refresh" width="14" height="14" align="middle">'
                f"&nbsp;{html.escape(self._retry_label)}</a></p>"
            )
        parts.append("</body></html>")
        self._browser.setHtml("".join(parts))
        if self._streaming:
            cursor = QTextCursor(self._browser.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self._stream_start = cursor.block().position()
            cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            self._insert_stream(cursor)
        if retry:
            # After setHtml, which resets the document; images load on layout.
            self._add_icon_resource("refresh")
        if preserve_scroll:
            self._browser.verticalScrollBar().setValue(scroll)
        else:
            self._scroll_browser_to_end()
            QTimer.singleShot(0, self._scroll_browser_to_end)

    def _add_icon_resource(self, name: str) -> None:
        ratio = max(1.0, self.devicePixelRatioF())
        side = round(14 * ratio)
        # Disabled mode draws in the secondary text color, matching the link.
        pixmap = icon(name).pixmap(QSize(side, side), QIcon.Mode.Disabled)
        pixmap.setDevicePixelRatio(ratio)
        self._browser.document().addResource(
            QTextDocument.ResourceType.ImageResource, QUrl(f"icon:{name}"), pixmap
        )
