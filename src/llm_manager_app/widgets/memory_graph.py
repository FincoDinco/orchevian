"""Interactive native Qt graph of Markdown note links."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QBrush, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QWidget,
)

from llm_engine.store.vault import MemoryNote, MemoryVault
from llm_manager_app.tokens import current_palette, qcolor


class _Node(QGraphicsEllipseItem):
    def __init__(self, note: MemoryNote, graph: MemoryGraph) -> None:
        super().__init__(-9, -9, 18, 18)
        self.note = note
        self.graph = graph
        self.edges: list[tuple[QGraphicsLineItem, _Node]] = []
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(note.title + "\nDouble-click to open · Drag to arrange")
        palette = current_palette()
        self.setPen(QPen(qcolor(palette.canvas), 2))
        self.setBrush(
            QBrush(qcolor(palette.secondary if note.kind == "source" else palette.accent))
        )
        label = QGraphicsSimpleTextItem(
            note.title[:32] + ("…" if len(note.title) > 32 else ""), self
        )
        label.setBrush(QBrush(qcolor(palette.text)))
        label.setPos(-label.boundingRect().width() / 2, 16)
        self.setZValue(1)

    def mouseDoubleClickEvent(self, event) -> None:
        self.graph.note_activated.emit(self.note.key)
        event.accept()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for edge, other in self.edges:
                edge.setLine(self.pos().x(), self.pos().y(), other.pos().x(), other.pos().y())
        return super().itemChange(change, value)


class MemoryGraph(QGraphicsView):
    note_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("memoryGraph")
        self.setAccessibleName("Note connections graph")
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self._nodes: dict[str, _Node] = {}

    def set_notes(self, notes: list[MemoryNote], vault: MemoryVault, selected: str | None) -> None:
        scene = self.scene()
        scene.clear()
        self._nodes = {}
        palette = current_palette()
        self.setBackgroundBrush(QBrush(qcolor(palette.canvas)))
        edges = vault.connections(notes)
        # Bound scene work, prioritizing the selected note and its immediate neighbors.
        neighbors = {key for a, b in edges if selected in (a, b) for key in (a, b)}
        visible = sorted(notes, key=lambda note: (note.key not in neighbors, note.key))[:200]
        if not visible:
            item = scene.addText(
                "Your ideas will connect here.\nCreate a note or remember a conversation."
            )
            item.setDefaultTextColor(qcolor(palette.secondary))
        count = len(visible)
        radius = max(140, count * 20)
        for index, note in enumerate(visible):
            node = _Node(note, self)
            if note.key == selected:
                node.setBrush(QBrush(qcolor(palette.text)))
            angle = 2 * math.pi * index / max(1, count) - math.pi / 2
            node.setPos(QPointF(radius * math.cos(angle), radius * math.sin(angle)))
            scene.addItem(node)
            self._nodes[note.key] = node
        drawn = set()
        for source, target in sorted(edges):
            pair = tuple(sorted((source, target)))
            if pair in drawn or source not in self._nodes or target not in self._nodes:
                continue
            drawn.add(pair)
            a, b = self._nodes[source], self._nodes[target]
            color = palette.accent if selected in pair else palette.secondary
            pen = QPen(qcolor(color), 1.2)
            edge = scene.addLine(a.x(), a.y(), b.x(), b.y(), pen)
            edge.setOpacity(0.65 if selected in pair else 0.3)
            a.edges.append((edge, b))
            b.edges.append((edge, a))
        if len(notes) > len(visible):
            item = scene.addText(
                f"Showing {len(visible)} of {len(notes)} notes. Search to explore more."
            )
            item.setDefaultTextColor(qcolor(palette.secondary))
            item.setPos(-radius, radius + 75)
        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-50, -50, 50, 50))
        self.fit_graph()

    def fit_graph(self) -> None:
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.fit_graph()

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        scale = self.transform().m11() * factor
        if 0.08 <= scale <= 5:
            self.scale(factor, factor)
        event.accept()
