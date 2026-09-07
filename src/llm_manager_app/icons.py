"""Small, palette-aware line icons drawn by Qt at the display's native scale."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPen, QPixmap, QPolygonF

from llm_manager_app.tokens import current_palette, qcolor


class _LineIcon(QIconEngine):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    def clone(self) -> QIconEngine:
        return _LineIcon(self.name)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        del state
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(rect.x(), rect.y())
        painter.scale(rect.width() / 24, rect.height() / 24)
        palette = current_palette()
        color = palette.secondary if mode == QIcon.Mode.Disabled else palette.text
        painter.setPen(
            QPen(
                qcolor(color),
                1.6,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.name == "chat":
            painter.drawRoundedRect(QRectF(4, 4, 16, 13), 3, 3)
            painter.drawLine(7, 17, 7, 21)
            painter.drawLine(7, 21, 12, 17)
        elif self.name == "models":
            painter.drawRoundedRect(QRectF(6, 6, 12, 12), 2, 2)
            painter.drawRect(QRectF(10, 10, 4, 4))
            for n in (9, 15):
                painter.drawLine(n, 3, n, 6)
                painter.drawLine(n, 18, n, 21)
                painter.drawLine(3, n, 6, n)
                painter.drawLine(18, n, 21, n)
        elif self.name == "brain":
            for a, b in (
                (QPointF(6, 6), QPointF(18, 8)),
                (QPointF(6, 6), QPointF(10, 19)),
                (QPointF(18, 8), QPointF(10, 19)),
            ):
                painter.drawLine(a, b)
            for x, y in ((6, 6), (18, 8), (10, 19)):
                painter.drawEllipse(QPointF(x, y), 3, 3)
        elif self.name == "note":
            painter.drawRoundedRect(QRectF(5, 3, 14, 18), 2, 2)
            painter.drawLine(9, 8, 15, 8)
            painter.drawLine(9, 12, 15, 12)
            painter.drawLine(9, 16, 13, 16)
        elif self.name == "folder":
            painter.drawPolyline(
                QPolygonF(
                    [
                        QPointF(x, y)
                        for x, y in (
                            (3, 7),
                            (3, 19),
                            (21, 19),
                            (21, 7),
                            (12, 7),
                            (10, 4),
                            (3, 4),
                            (3, 7),
                        )
                    ]
                )
            )
        elif self.name == "plus":
            painter.drawLine(5, 12, 19, 12)
            painter.drawLine(12, 5, 12, 19)
        elif self.name == "search":
            painter.drawEllipse(QRectF(4, 4, 11, 11))
            painter.drawLine(14, 14, 20, 20)
        elif self.name == "settings":
            for y, x in ((6, 9), (12, 16), (18, 7)):
                painter.drawLine(3, y, x - 2, y)
                painter.drawLine(x + 2, y, 21, y)
                painter.drawEllipse(QRectF(x - 2, y - 2, 4, 4))
        elif self.name == "refresh":
            painter.drawArc(QRectF(5, 5, 14, 14), 40 * 16, 285 * 16)
            painter.drawLine(20, 4, 20, 10)
            painter.drawLine(20, 10, 14, 10)
        elif self.name == "arrow":
            painter.drawLine(5, 12, 19, 12)
            painter.drawLine(14, 7, 19, 12)
            painter.drawLine(14, 17, 19, 12)
        painter.restore()

    def pixmap(self, size, mode, state):
        result = QPixmap(size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return result


def icon(name: str) -> QIcon:
    return QIcon(_LineIcon(name))
