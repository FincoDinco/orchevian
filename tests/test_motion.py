from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QToolBar  # noqa: E402

from llm_manager_app import motion, tokens  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def _spin(app, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _toolbar_pill():
    from llm_manager_app.widgets.model_activity import ModelActivity

    window = QMainWindow()
    bar = QToolBar()
    window.addToolBar(bar)
    pill = ModelActivity(bar)
    pill.bind_action(bar.addWidget(pill))
    after = QLabel("after")
    bar.addWidget(after)
    window.resize(900, 200)
    window.show()
    return window, pill, after


def test_reduced_motion_is_a_bool_and_makes_fades_instant(monkeypatch):
    app = _app()
    assert isinstance(motion.prefers_reduced_motion(), bool)
    monkeypatch.setattr(motion, "prefers_reduced_motion", lambda: True)
    label = QLabel("x")
    motion.fade(label, show=True)
    assert not label.isHidden()
    motion.fade(label, show=False)
    assert label.isHidden()
    assert label.graphicsEffect() is None
    app.processEvents()


def test_activity_pill_skips_quick_work_and_fades_through_the_toolbar(monkeypatch):
    app = _app()
    monkeypatch.setattr(motion, "prefers_reduced_motion", lambda: False)
    window, pill, after = _toolbar_pill()
    try:
        app.processEvents()
        start = after.x()
        pill.set_activity(True, "Quick")
        _spin(app, 120)
        pill.set_activity(False, "")
        _spin(app, 400)
        assert not pill.isVisible(), "near-instant work should never flash the pill"

        pill.set_activity(True, "Loading model…")
        _spin(app, motion.ACTIVITY_DELAY_MS + motion.ENTER_MS + 150)
        assert pill.isVisible()
        assert after.x() > start, "the shown pill reserves its toolbar space"
        pill.set_activity(False, "")
        _spin(app, motion.EXIT_MS + 150)
        window.resize(910, 200)
        _spin(app, 30)
        assert not pill.isVisible(), "a toolbar relayout must not bring it back"

        pill.set_activity(True, "Stopping", stopping=True)
        _spin(app, 20)
        assert pill.isVisible(), "a stop request is reachable at once"
    finally:
        window.close()


def test_spinner_angle_follows_elapsed_time():
    from llm_manager_app.widgets.model_activity import ActivitySpinner

    app = _app()
    spinner = ActivitySpinner()
    spinner.show()
    first = spinner._angle
    _spin(app, 200)
    moved = (spinner._angle - first) % 360
    # ~200 ms of an 800 ms turn is ~90 degrees; frame timing may add a little.
    assert 45 <= moved <= 180
    spinner.close()


def test_stylesheet_separates_hover_selection_and_press():
    for palette in (tokens.LIGHT, tokens.DARK):
        sheet = tokens.qss(palette)
        assert "QTreeView#sidebarNav::item:hover" in sheet
        hover = sheet.split("QTreeView#sidebarNav::item:hover {")[1].split("}")[0]
        assert palette.selection not in hover
        assert "QPushButton#sendButton:pressed" in sheet
        assert "QCheckBox::indicator:checked" in sheet
        assert 'QLabel#chatBanner[tone="error"]' in sheet
