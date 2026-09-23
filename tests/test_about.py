from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit  # noqa: E402

from llm_manager_app import __version__  # noqa: E402
from llm_manager_app.widgets.about import AboutDialog, license_path  # noqa: E402


def test_about_shows_version_copyright_license_and_no_warranty():
    app = QApplication.instance() or QApplication([])
    dialog = AboutDialog()
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert f"Version {__version__}" in text
    assert "Copyright © 2026 Seth Hardin" in text
    assert "GNU General Public License" in text and "WITHOUT ANY WARRANTY" in text
    path = license_path()
    assert path is not None and "GNU GENERAL PUBLIC LICENSE" in path.read_text()
    dialog.license_button.click()
    app.processEvents()
    shown = dialog.findChild(QPlainTextEdit, "licenseText")
    assert shown is not None and "Version 3, 29 June 2007" in shown.toPlainText()
    dialog.close()


def test_about_is_in_the_help_menu_with_the_macos_role(tmp_path):
    from test_streaming_chat import _qapp, _window

    _qapp()
    window, store, library, _ = _window(tmp_path)
    try:
        action = window.findChild(QAction, "aboutAction")
        assert action is not None and action.menuRole() == QAction.MenuRole.AboutRole
        action.trigger()
        assert window._about_dialog.isVisible()
    finally:
        window.close()
        store.close()
