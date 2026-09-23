"""Help → About Orchevian: version, copyright, license, and source code."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llm_manager_app import __version__

SOURCE_URL = "https://github.com/FincoDinco/orchevian"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
COPYRIGHT = "Copyright © 2026 Seth Hardin"
# The notice the GPL recommends that interactive programs show.
NOTICE = (
    "Orchevian is free software: you can redistribute it and/or modify it under the terms "
    "of the GNU General Public License as published by the Free Software Foundation, either "
    "version 3 of the License, or (at your option) any later version.\n\n"
    "Orchevian is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; "
    "without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR "
    "PURPOSE. See the GNU General Public License for more details."
)


def _bundled(name: str) -> Path | None:
    """A file shipped beside the app (desktop builds) or in the source checkout."""
    roots = [Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parents[3]]
    for root in roots:
        candidate = root / name
        if str(root) and candidate.is_file():
            return candidate
    return None


def license_path() -> Path | None:
    return _bundled("LICENSE")


def notices_path() -> Path | None:
    """Third-party notices exist in desktop builds; a source checkout uses its own packages."""
    return _bundled("THIRD_PARTY_NOTICES.txt") if getattr(sys, "frozen", False) else None


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aboutDialog")
        self.setWindowTitle("About Orchevian")

        name = QLabel("Orchevian", self)
        name.setObjectName("welcomeTitle")
        version = QLabel(f"Version {__version__}", self)
        version.setObjectName("aboutVersion")
        version.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tagline = QLabel("A private, local-first AI workspace.", self)
        tagline.setObjectName("pageSubtitle")
        copyright_label = QLabel(COPYRIGHT, self)
        notice = QLabel(NOTICE, self)
        notice.setObjectName("settingsHint")
        notice.setWordWrap(True)
        notice.setFixedWidth(460)  # Wrapped text needs a width to compute its height.

        self.license_button = QPushButton("View License", self)
        self.license_button.clicked.connect(
            lambda: self._show_text(license_path(), "GNU General Public License v3.0", LICENSE_URL)
        )
        self.notices_button = QPushButton("Third-Party Notices", self)
        self.notices_button.setVisible(notices_path() is not None)
        self.notices_button.clicked.connect(
            lambda: self._show_text(notices_path(), "Third-Party Notices", SOURCE_URL)
        )
        source = QPushButton("Source Code", self)
        source.setToolTip(SOURCE_URL)
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SOURCE_URL)))
        close = QPushButton("Close", self)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(self.license_button)
        buttons.addWidget(self.notices_button)
        buttons.addWidget(source)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(6)
        layout.addWidget(name)
        layout.addWidget(version)
        layout.addWidget(tagline)
        layout.addSpacing(12)
        layout.addWidget(copyright_label)
        layout.addSpacing(6)
        layout.addWidget(notice)
        layout.addSpacing(16)
        layout.addLayout(buttons)

    def show_license(self) -> None:
        self.license_button.click()

    def _show_text(self, path: Path | None, title: str, fallback_url: str) -> None:
        if path is None:
            QDesktopServices.openUrl(QUrl(fallback_url))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(640, 560)
        text = QPlainTextEdit(dialog)
        text.setObjectName("licenseText")
        text.setReadOnly(True)
        text.setPlainText(path.read_text(encoding="utf-8"))
        text.setFont(QFont("Menlo" if sys.platform == "darwin" else "monospace", 11))
        close = QPushButton("Close", dialog)
        close.clicked.connect(dialog.accept)
        layout = QVBoxLayout(dialog)
        layout.addWidget(text)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.open()
