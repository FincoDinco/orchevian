"""Shared UI building blocks: page header, segmented control, status badge,
and a Reveal-in-Finder helper.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QProcess, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PageHeader(QWidget):
    """Quiet, Mac-native page header: optional eyebrow, serif title, faint meta slot."""

    def __init__(
        self,
        title: str,
        actions: list[QWidget] | None = None,
        eyebrow: str | None = None,
        rule: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("pageHeader")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 0)
        outer.setSpacing(2)

        if eyebrow:
            eb = QLabel(eyebrow.upper())
            eb.setObjectName("pageEyebrow")
            outer.addWidget(eb)
            outer.addSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(12)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("pageTitle")
        row.addWidget(title_lbl)

        self._meta = QLabel("")
        self._meta.setObjectName("runningHead")
        self._meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._meta, 1)

        for w in actions or []:
            row.addWidget(w)
        outer.addLayout(row)

        outer.addSpacing(14)
        if rule:
            line = QFrame()
            line.setObjectName("headerRule")
            line.setFixedHeight(1)
            outer.addWidget(line)

    def set_meta(self, text: str) -> None:
        self._meta.setText(text)


class SegmentedControl(QWidget):
    """A rounded track of mutually-exclusive segments. Emits the chosen label."""

    currentChanged = Signal(str)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        track = QWidget()
        track.setObjectName("segmentTrack")
        row = QHBoxLayout(track)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for i, label in enumerate(labels):
            btn = QPushButton(label)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if i == 0:
                btn.setChecked(True)
            self._group.addButton(btn, i)
            row.addWidget(btn)

        outer.addWidget(track)
        outer.addStretch()

        self._labels = labels
        self._group.idClicked.connect(lambda idx: self.currentChanged.emit(self._labels[idx]))

    def set_current(self, label: str) -> None:
        if label in self._labels:
            self._group.button(self._labels.index(label)).setChecked(True)


class StatusBadge(QLabel):
    """A coloured word ('Running' / 'Stopped') driven by a QSS state property."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusBadge")
        self.set_state("stopped")

    def set_state(self, state: str) -> None:
        self.setProperty("state", state)
        self.setText("●  Running" if state == "running" else "●  Stopped")
        self.style().unpolish(self)
        self.style().polish(self)


class Disclosure(QWidget):
    """A collapsible section: a '▸ Title' toggle above a content widget (hidden by default)."""

    def __init__(self, title: str, content: QWidget, parent=None):
        super().__init__(parent)
        self._title = title
        self._content = content
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._btn = QPushButton(f"▸  {title}")
        self._btn.setObjectName("discloseBtn")
        self._btn.setCheckable(True)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.toggled.connect(self._on_toggled)
        layout.addWidget(self._btn)
        layout.addWidget(content)
        content.setVisible(False)

    def _on_toggled(self, open_: bool):
        self._btn.setText(f"{'▾' if open_ else '▸'}  {self._title}")
        self._content.setVisible(open_)

    def is_open(self) -> bool:
        return self._btn.isChecked()


# --- Icons -------------------------------------------------------------------

_ICON_CACHE: dict[tuple[str, str], QIcon] = {}

# 24x24 line glyphs, stroke = currentColor
NAV_ICONS = {
    "chat": '<path d="M4 5h16v11H8l-4 4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
    "cube": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
    "disk": '<rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M14 5v4h-4V5" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    "page": '<path d="M6 3h8l4 4v14H6z M14 3v4h4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/><path d="M9 12h6 M9 16h6" stroke="currentColor" stroke-width="1.6"/>',
    "plug": '<path d="M9 3v5 M15 3v5 M6 8h12v3a6 6 0 0 1-12 0z M12 17v4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
}


def svg_icon(name_or_svg: str, color: str, size: int = 24) -> QIcon:
    svg = NAV_ICONS.get(name_or_svg, name_or_svg).replace("currentColor", color)
    key = (svg, color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    doc = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{svg}</svg>'
    renderer = QSvgRenderer(QByteArray(doc.encode()))
    pix = QPixmap(QSize(size, size))
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    icon = QIcon(pix)
    _ICON_CACHE[key] = icon
    return icon


def reveal_in_finder(path: Path | str) -> None:
    """Open the given path in Finder, selecting it."""
    p = Path(path)
    target = p if p.exists() else p.parent
    QProcess.startDetached("open", ["-R", str(target)])
