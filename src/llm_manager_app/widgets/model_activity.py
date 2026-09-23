"""Quiet, persistent workspace-header activity with an immediate stop action."""

import time

from PySide6.QtCore import QElapsedTimer, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from llm_manager_app.icons import icon
from llm_manager_app.motion import ACTIVITY_DELAY_MS, fade, prefers_reduced_motion
from llm_manager_app.tokens import current_palette, qcolor
from llm_manager_app.widgets.labels import ElidedLabel


class ActivitySpinner(QWidget):
    """A small rotating arc; a steady dot when reduced motion is preferred."""

    # One turn per 0.8 s: quick spinners make waits feel shorter.
    PERIOD_MS = 800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        # Redraw at display rate; the angle comes from elapsed time, so a
        # late frame never slows the rotation.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

    @property
    def _angle(self):
        if not self._clock.isValid():
            return 0
        return self._clock.elapsed() % self.PERIOD_MS * 360 // self.PERIOD_MS

    def showEvent(self, event):
        super().showEvent(event)
        self._clock.start()
        if not prefers_reduced_motion():
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = qcolor(current_palette().accent)
        if prefers_reduced_motion():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(4, 4, 6, 6))
            return
        track = qcolor(current_palette().separator)
        rect = QRectF(2, 2, 10, 10)
        painter.setPen(QPen(track, 1.6))
        painter.drawEllipse(rect)
        pen = QPen(accent, 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, (90 - self._angle) * 16, -100 * 16)


class ModelActivity(QFrame):
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modelActivity")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMaximumWidth(420)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._started = None
        self._busy = False
        # Near-instant work never shows the pill, so it does not flash.
        self._reveal = QTimer(self)
        self._reveal.setSingleShot(True)
        self._reveal.setInterval(ACTIVITY_DELAY_MS)
        self._reveal.timeout.connect(lambda: self._fade(True))
        self._set_visible = self.setVisible
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 3, 0)
        layout.setSpacing(8)
        self.spinner = ActivitySpinner(self)
        layout.addWidget(self.spinner)
        self.label = ElidedLabel("", self)
        self.label.setMinimumWidth(120)
        self.label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.label, 1)
        self.elapsed = QLabel(self)
        self.elapsed.setObjectName("activityElapsed")
        layout.addWidget(self.elapsed)
        divider = QFrame(self)
        divider.setObjectName("activityDivider")
        divider.setFixedSize(1, 16)
        layout.addWidget(divider)
        self.stop = QPushButton("Stop", self)
        self.stop.setIcon(icon("stop"))
        self.stop.setIconSize(QSize(12, 12))
        self.stop.setFixedHeight(24)
        self.stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop.setObjectName("forceStopModelButton")
        self.stop.setToolTip("Force stop model loading or inference · Esc")
        self.stop.setAccessibleName("Force stop model")
        self.stop.clicked.connect(self.stop_requested)
        layout.addWidget(self.stop)
        self.hide()

    def bind_action(self, action) -> None:
        """In a QToolBar the action owns visibility; fade through it."""
        self._set_visible = action.setVisible
        action.setVisible(False)

    def _fade(self, show):
        fade(self, show=show, set_visible=self._set_visible)

    def set_activity(self, busy, text, stopping=False):
        if busy and self._started is None:
            self._started = time.monotonic()
        elif not busy:
            self._started = None
        seconds = int(time.monotonic() - self._started) if self._started is not None else 0
        self.elapsed.setText(f"{seconds // 60}:{seconds % 60:02d}")
        if busy and not self._busy:
            # A stop request must be reachable at once, not after the delay.
            if stopping:
                self._fade(True)
            else:
                self._reveal.start()
        elif not busy and self._busy:
            self._reveal.stop()
            if self.isVisible():
                self._fade(False)
        elif busy and stopping and self._reveal.isActive():
            self._reveal.stop()
            self._fade(True)
        self._busy = busy
        self.label.setText(text)
        self.label.setMaximumWidth(
            min(240, max(120, self.label.fontMetrics().horizontalAdvance(text) + 4))
        )
        self.label.setToolTip(text)
        self.stop.setText("Stopping…" if stopping else "Stop")
        self.stop.setEnabled(busy and not stopping)
