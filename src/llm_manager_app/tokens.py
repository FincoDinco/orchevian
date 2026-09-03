"""Studio visual tokens: named colors and a small QSS string."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True, slots=True)
class StudioPalette:
    canvas: str
    elevated: str
    text: str
    secondary: str
    accent: str
    danger: str
    selection: str
    separator: str
    radius_control: int = 8
    radius_composer: int = 10


# DESIGN.md Visual System — Studio
DARK = StudioPalette(
    canvas="#1C1C1E",
    elevated="#2C2C2E",
    text="#F5F5F7",
    secondary="#8E8E93",
    accent="#5B8DEF",
    danger="#FF453A",
    selection="rgba(91, 141, 239, 56)",
    separator="rgba(255, 255, 255, 31)",
)

LIGHT = StudioPalette(
    canvas="#F2F2F7",
    elevated="#FFFFFF",
    text="#1C1C1E",
    secondary="#6C6C70",
    accent="#3B6FDB",
    danger="#FF3B30",
    selection="rgba(59, 111, 219, 46)",
    separator="rgba(0, 0, 0, 20)",
)


def system_font_family() -> str:
    if sys.platform == "darwin":
        return ".AppleSystemUIFont"
    if sys.platform == "win32":
        return "Segoe UI"
    return "sans-serif"


def qss(palette: StudioPalette) -> str:
    family = system_font_family()
    return f"""
    QMainWindow, QWidget#shell {{
        background-color: {palette.canvas};
        color: {palette.text};
        font-family: "{family}";
    }}
    QWidget#sidebar, QListWidget#sidebarNav {{
        background-color: {palette.canvas};
        color: {palette.text};
        border: none;
        outline: none;
    }}
    QListWidget#sidebarNav::item {{
        padding: 8px 12px;
        margin: 0 8px 4px 8px;
        border-radius: {palette.radius_control}px;
    }}
    QListWidget#sidebarNav::item:selected {{
        background-color: {palette.selection};
        color: {palette.text};
    }}
    QWidget#listPane, QListView#listPane {{
        background-color: {palette.canvas};
        color: {palette.text};
        border: none;
    }}
    QWidget#detailPane, QLabel#detailPane {{
        background-color: {palette.canvas};
        color: {palette.secondary};
        border: none;
    }}
    QSplitter::handle {{
        background-color: {palette.separator};
        width: 1px;
    }}
    """


def palette_for_app(app: QApplication) -> StudioPalette:
    window = app.palette().color(QPalette.ColorRole.Window)
    return DARK if window.lightness() < 128 else LIGHT


def apply_studio(app: QApplication, palette: StudioPalette | None = None) -> StudioPalette:
    chosen = palette if palette is not None else palette_for_app(app)
    app.setFont(QFont(system_font_family()))
    app.setStyleSheet(qss(chosen))
    return chosen
