"""Shared motion: reduced-motion detection, easing, durations and a fade helper.

Motion is reserved for occasional, spatial moments (popovers, the activity pill).
Frequent and keyboard-driven actions stay instant.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from functools import cache

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

# Enter/exit durations: exits are faster than entrances.
ENTER_MS = 160
EXIT_MS = 110
# Show the activity pill only for work that is not near-instant.
ACTIVITY_DELAY_MS = 300


def ease_out() -> QEasingCurve:
    """Strong ease-out, cubic-bezier(0.23, 1, 0.32, 1): immediate, then settles."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1.0, 1.0))
    return curve


@cache
def _macos_reduce_motion():
    """Return a callable reading NSWorkspace's Reduce Motion flag, or None."""
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("AppKit"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc)
        )
        flag = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc)
        )
        workspace = send(objc.objc_getClass(b"NSWorkspace"),
                         objc.sel_registerName(b"sharedWorkspace"))
        selector = objc.sel_registerName(b"accessibilityDisplayShouldReduceMotion")
        if not workspace or not selector:
            return None
        return lambda: bool(flag(workspace, selector))
    except (OSError, AttributeError, TypeError):
        return None


def _windows_reduce_motion() -> bool | None:
    try:
        enabled = ctypes.c_bool()
        # SPI_GETCLIENTAREAANIMATION: off when "Show animations in Windows" is off.
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return not enabled.value
    except (AttributeError, OSError):
        pass
    return None


def prefers_reduced_motion() -> bool:
    """The OS Reduce Motion setting, read live so a change applies without restart."""
    if sys.platform == "darwin":
        reader = _macos_reduce_motion()
        if reader is not None:
            return reader()
    elif sys.platform == "win32":
        value = _windows_reduce_motion()
        if value is not None:
            return value
    app = QGuiApplication.instance()
    # Elsewhere, a disabled cursor blink is the closest available accessibility hint.
    return app is not None and app.styleHints().cursorFlashTime() <= 0


def fade(widget: QWidget, *, show: bool, set_visible=None) -> None:
    """Fade a widget in or out; instant under reduced motion.

    `set_visible` replaces widget.setVisible where something else owns
    visibility, e.g. the QAction of a widget placed in a QToolBar.
    """
    set_visible = set_visible or widget.setVisible
    running = getattr(widget, "_motion_fade", None)
    if running is not None:
        running.stop()
    if prefers_reduced_motion():
        widget.setGraphicsEffect(None)
        set_visible(show)
        return
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0 if not widget.isVisible() else 1.0)
        widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    # Start from the current value so a reversal mid-fade does not jump.
    animation.setStartValue(effect.opacity() if widget.isVisible() else 0.0)
    animation.setEndValue(1.0 if show else 0.0)
    animation.setDuration(ENTER_MS if show else EXIT_MS)
    animation.setEasingCurve(ease_out())

    def finished():
        widget._motion_fade = None
        if not show:
            set_visible(False)
        # Drop the effect once opaque: it renders the widget offscreen otherwise.
        widget.setGraphicsEffect(None)

    animation.finished.connect(finished)
    widget._motion_fade = animation
    if show:
        set_visible(True)
    animation.start()
