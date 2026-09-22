"""Quiet, persistent workspace-header activity with an immediate stop action."""

import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from llm_manager_app.icons import icon
from llm_manager_app.widgets.labels import ElidedLabel


class ModelActivity(QWidget):
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelActivity")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMaximumWidth(460)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._started = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.label = ElidedLabel("", self)
        self.label.setMinimumWidth(140)
        self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.label, 1)
        self.elapsed = QLabel(self)
        self.elapsed.setObjectName("activityElapsed")
        layout.addWidget(self.elapsed)
        self.stop = QPushButton("Force stop", self)
        self.stop.setIcon(icon("stop"))
        self.stop.setIconSize(QSize(14, 14))
        self.stop.setFixedHeight(32)
        self.stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop.setObjectName("forceStopModelButton")
        self.stop.setToolTip("Stop model loading or inference · Esc")
        self.stop.setAccessibleName("Force stop model")
        self.stop.clicked.connect(self.stop_requested)
        layout.addWidget(self.stop)
        self.hide()

    def set_activity(self, busy, text, stopping=False):
        if busy and self._started is None:
            self._started = time.monotonic()
        elif not busy:
            self._started = None
        seconds = int(time.monotonic() - self._started) if self._started is not None else 0
        self.elapsed.setText(f"{seconds // 60}:{seconds % 60:02d}")
        self.setVisible(busy)
        self.label.setText(text)
        self.label.setMaximumWidth(
            min(260, max(140, self.label.fontMetrics().horizontalAdvance(text) + 4))
        )
        self.label.setToolTip(text)
        self.stop.setText("Stopping…" if stopping else "Force stop")
        self.stop.setEnabled(busy and not stopping)
