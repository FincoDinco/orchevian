"""Studio visual tokens: named colors and a small QSS string."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget


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
    sidebar: str
    radius_control: int = 8
    radius_composer: int = 14


# DESIGN.md Visual System — Studio
DARK = StudioPalette(
    canvas="#1B1D22",
    elevated="#25282F",
    text="#EDEEF2",
    secondary="#9A9FAA",
    accent="#8CA9FF",
    danger="#FF453A",
    selection="rgba(140, 169, 255, 32)",
    separator="rgba(255, 255, 255, 22)",
    sidebar="#15171B",
)

LIGHT = StudioPalette(
    canvas="#FAFAF8",
    elevated="#FFFFFF",
    text="#252830",
    secondary="#666B76",
    accent="#4467C4",
    danger="#FF3B30",
    selection="rgba(68, 103, 196, 24)",
    separator="rgba(0, 0, 0, 20)",
    sidebar="#EFF0ED",
)

_CURRENT: StudioPalette | None = None


def named_tab_width(widget: QWidget, names: tuple[str, ...], *, extra: int = 24) -> int:
    """Minimum width of one named tab: longest label plus chrome, not the expanded pane."""
    metrics = widget.fontMetrics()
    widest = max((metrics.horizontalAdvance(name) for name in names), default=0)
    return max(widest + extra, extra)


def system_font_family() -> str:
    if sys.platform == "darwin":
        return ".AppleSystemUIFont"
    if sys.platform == "win32":
        return "Segoe UI"
    return "sans-serif"


def mono_font_family() -> str:
    if sys.platform == "darwin":
        return "Menlo"
    if sys.platform == "win32":
        return "Consolas"
    return "monospace"


def qcolor(value: str) -> QColor:
    color = QColor(value)
    if color.isValid() and not value.strip().lower().startswith("rgba"):
        return color
    text = value.strip()
    if text.lower().startswith("rgba(") and text.endswith(")"):
        parts = [part.strip() for part in text[5:-1].split(",")]
        if len(parts) == 4:
            try:
                red, green, blue, alpha = (int(float(part)) for part in parts)
            except ValueError:
                return QColor(0, 0, 0)
            parsed = QColor(red, green, blue, alpha)
            if parsed.isValid():
                return parsed
    return color if color.isValid() else QColor(0, 0, 0)


def current_palette() -> StudioPalette:
    if _CURRENT is not None:
        return _CURRENT
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return palette_for_app(app)
    return DARK


def qss(palette: StudioPalette) -> str:
    family = system_font_family()
    mono = mono_font_family()
    r = palette.radius_control
    return f"""
    QMainWindow, QDialog, QWidget#shell {{
        background-color: {palette.canvas}; color: {palette.text}; font-family: "{family}";
    }}
    QWidget {{ color: {palette.text}; font-family: "{family}"; }}
    QWidget#sidebar, QTreeView#sidebarNav {{
        background-color: {palette.sidebar}; color: {palette.text}; border: none; outline: none;
    }}
    QWidget#listPane, QListView#conversationView, QListWidget#modelsList,
    QWidget#detailPane, QWidget#transcript, QWidget#chatBody {{
        background-color: {palette.canvas}; color: {palette.text}; border: none; outline: none;
    }}
    QTreeView#sidebarNav::item {{
        padding: 7px 10px; margin: 1px 8px; border-radius: {r}px; min-height: 22px;
    }}
    QTreeView#sidebarNav::item:hover, QTreeView#sidebarNav::item:selected {{
        background-color: {palette.selection}; color: {palette.text};
    }}
    QTreeView#sidebarNav::branch {{ background: {palette.sidebar}; }}
    QListView#conversationView::item, QListWidget#modelsList::item {{
        padding: 8px; margin: 1px 4px; border-radius: {r}px; background: transparent;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {palette.elevated}; color: {palette.text};
        border: none; border-radius: {r}px; padding: 6px 8px;
        selection-background-color: {palette.selection}; selection-color: {palette.text};
    }}
    QWidget#inspector QSpinBox, QWidget#inspector QDoubleSpinBox,
    QWidget#inspector QPlainTextEdit#systemPromptEdit {{
        background-color: {palette.canvas}; color: {palette.text};
        border: none; border-radius: {r}px; padding: 6px;
    }}
    QPushButton {{
        background-color: {palette.elevated}; color: {palette.text};
        border: 1px solid {palette.separator}; border-radius: {r}px; padding: 8px 12px;
    }}
    QPushButton:hover {{ background-color: {palette.selection}; }}
    QPushButton:disabled {{ color: {palette.secondary}; }}
    QPushButton#newChatButton, QPushButton#openModelsButton,
    QPushButton#regenerateButton, QPushButton#inspectorToggle, QPushButton#inspectorTab {{
        background-color: transparent; color: {palette.accent}; border: none; padding: 6px 8px;
    }}
    QPushButton#newChatButton:hover, QPushButton#openModelsButton:hover,
    QPushButton#regenerateButton:hover, QPushButton#inspectorToggle:hover,
    QPushButton#inspectorTab:hover {{
        background-color: {palette.selection};
    }}
    QPushButton#inspectorToggle:checked, QPushButton#inspectorTab:checked {{
        color: {palette.text};
    }}
    QLabel#listEmpty, QLabel#chatEmpty, QLabel#modelEmpty,
    QLabel#lastTurnLabel, QLabel#settingsHint, QLabel#inspectorSection {{
        color: {palette.secondary}; background: transparent;
    }}
    QLabel#lastTurnLabel {{ font-family: "{mono}"; }}
    QToolButton#sidebarCollapse, QToolButton#inspectorCollapse, QToolButton#newProjectButton {{
        background: transparent; color: {palette.secondary}; border: none; padding: 4px;
    }}
    QToolButton#sidebarCollapse:hover, QToolButton#inspectorCollapse:hover {{
        background-color: {palette.selection}; color: {palette.text};
    }}
    QToolButton#modelPicker {{
        background: transparent; color: {palette.text}; border: none;
        padding: 6px 10px; border-radius: {r}px;
    }}
    QToolButton#modelPicker:hover, QToolButton#modelPicker:pressed {{
        background-color: {palette.selection};
    }}
    QTextBrowser#transcriptHistory, QPlainTextEdit#transcriptStream {{
        background-color: {palette.canvas}; color: {palette.text}; border: none;
    }}
    QPlainTextEdit#composerEdit {{
        background-color: transparent; color: {palette.text};
        border: none; padding: 4px; font-size: 14px;
    }}
    QPushButton#sendButton {{
        background-color: {palette.accent}; color: {palette.canvas};
        border: none; border-radius: 10px; padding: 0; font-size: 20px; font-weight: 600;
    }}
    QPushButton#sendButton:hover {{ background-color: {palette.accent}; }}
    QPushButton#sendButton:disabled {{
        background-color: {palette.selection}; color: {palette.secondary};
    }}
    QPushButton#sendButton[mode="stop"] {{
        background-color: {palette.danger}; color: #FFFFFF; border-radius: 6px;
    }}
    QLabel#chatBanner, QLabel#settingsError, QLabel#modelsError, QLabel#modelsBanner {{
        color: {palette.danger}; background-color: {palette.elevated};
        border-radius: {r}px; padding: 8px 10px;
    }}
    QWidget#inspector {{
        background-color: {palette.elevated}; color: {palette.text}; border: none;
    }}
    QPushButton#presetPrecise, QPushButton#presetBalanced, QPushButton#presetCreative {{
        background-color: {palette.canvas}; color: {palette.secondary};
        border: none; border-radius: {r}px; padding: 6px 4px;
    }}
    QPushButton#presetPrecise:checked, QPushButton#presetBalanced:checked,
    QPushButton#presetCreative:checked {{
        background-color: {palette.selection}; color: {palette.text};
    }}
    QPushButton#unloadButton, QPushButton#restartButton {{ color: {palette.danger}; }}
    QPushButton#loadButton, QPushButton#chatButton {{
        background-color: {palette.accent}; color: {palette.canvas};
    }}
    QPushButton#unloadButton:disabled, QPushButton#restartButton:disabled {{
        color: {palette.secondary};
    }}
    QPushButton#loadButton:disabled, QPushButton#chatButton:disabled {{
        background: {palette.selection}; color: {palette.secondary};
    }}
    QSpinBox::up-button, QSpinBox::down-button,
    QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ border: none; width: 18px; }}
    QSplitter::handle {{ background-color: {palette.separator}; }}
    QMenu {{
        background-color: {palette.elevated}; color: {palette.text};
        border: none; padding: 4px; border-radius: {r}px;
    }}
    QMenu::item {{ padding: 6px 16px; border-radius: {r}px; }}
    QMenu::item:selected {{ background-color: {palette.selection}; }}
    QMenu::separator {{ height: 1px; background: {palette.separator}; margin: 4px 8px; }}
    QTabWidget::pane {{ border: none; background: {palette.canvas}; }}
    QTabBar::tab {{
        background: transparent; color: {palette.secondary}; padding: 8px 14px; border: none;
    }}
    QTabBar::tab:selected {{ color: {palette.text}; border-bottom: 2px solid {palette.accent}; }}
    QHeaderView::section, QAbstractItemView {{
        background-color: {palette.canvas}; color: {palette.text}; outline: none; border: none;
    }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {palette.separator}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: {palette.separator}; border-radius: 4px; min-width: 24px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background-color: {palette.elevated}; color: {palette.text};
        selection-background-color: {palette.selection}; border: none;
    }}
    QCheckBox {{ color: {palette.text}; spacing: 8px; }}
    QToolTip {{
        background-color: {palette.elevated}; color: {palette.text};
        border: none; padding: 4px 8px;
    }}
    QWidget#chatToolbar {{ background: transparent; border-bottom: 1px solid {palette.separator}; }}
    QWidget#sidebar QWidget#listPane, QWidget#sidebar QListView#conversationView {{
        background: {palette.sidebar};
    }}
    QLabel#brandTitle {{ font-size: 16px; font-weight: 600; letter-spacing: -0.3px; }}
    QLabel#eyebrow {{ color: {palette.secondary}; font-size: 10px;
        font-weight: 600; letter-spacing: 1px; }}
    QLabel#chatTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#pageTitle {{ font-size: 26px; font-weight: 600; letter-spacing: -0.6px; }}
    QLabel#modelTitle {{ font-size: 24px; font-weight: 600; letter-spacing: -0.5px; }}
    QLabel#pageSubtitle, QLabel#welcomeSubtitle {{ color: {palette.secondary}; font-size: 13px; }}
    QLabel#welcomeTitle {{ font-size: 28px; font-weight: 600; letter-spacing: -0.8px; }}
    QLabel#welcomeMark {{ color: {palette.accent}; font-size: 56px; }}
    QLabel#chatEmpty {{ font-size: 14px; }}
    QLabel#composerHint, QLabel#composerNote {{ color: {palette.secondary}; font-size: 11px; }}
    QWidget#composer {{ background: {palette.elevated}; border: 1px solid {palette.separator};
        border-radius: {palette.radius_composer}px; }}
    QPushButton#sidebarNewChat {{ background: {palette.elevated};
        text-align: left; padding: 10px 12px; }}
    QPushButton#sidebarSettings {{ background: transparent; border: none; padding: 6px; }}
    QPushButton#welcomeNewChat {{ background: {palette.accent};
        color: {palette.canvas}; border: none; }}
    QPushButton#starterButton {{ text-align: left; padding: 12px 16px; background: transparent; }}
    QPushButton#starterButton:hover {{ background: {palette.elevated};
        border-color: {palette.accent}; }}
    QLabel#catalogEmpty {{ color: {palette.secondary}; padding: 8px; }}
    QWidget#modelFacts {{ background: {palette.elevated}; border-radius: 10px; }}
    QLabel#factValue {{ font-size: 18px; font-weight: 600; }}
    QLabel#modelsDetailBody {{ color: {palette.secondary}; line-height: 1.6; }}
    QLineEdit:focus, QPushButton:focus, QToolButton:focus {{ border: 1px solid {palette.accent}; }}
    """


def palette_for_app(app: QApplication) -> StudioPalette:
    window = app.palette().color(QPalette.ColorRole.Window)
    return DARK if window.lightness() < 128 else LIGHT


def _qt_palette(studio: StudioPalette) -> QPalette:
    palette = QPalette()
    canvas = qcolor(studio.canvas)
    elevated = qcolor(studio.elevated)
    text = qcolor(studio.text)
    secondary = qcolor(studio.secondary)
    accent = qcolor(studio.accent)
    selection = qcolor(studio.selection)
    palette.setColor(QPalette.ColorRole.Window, canvas)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, elevated)
    palette.setColor(QPalette.ColorRole.AlternateBase, canvas)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, elevated)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, text)
    palette.setColor(QPalette.ColorRole.PlaceholderText, secondary)
    palette.setColor(QPalette.ColorRole.Highlight, accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, qcolor("#FFFFFF"))
    palette.setColor(QPalette.ColorRole.Link, accent)
    palette.setColor(QPalette.ColorRole.LinkVisited, accent)
    palette.setColor(QPalette.ColorRole.ToolTipBase, elevated)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    accent_role = getattr(QPalette.ColorRole, "Accent", None)
    if accent_role is not None:
        palette.setColor(accent_role, accent)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, secondary)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, secondary)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, secondary)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, selection)
    return palette


def apply_studio(
    app: QApplication,
    palette: StudioPalette | None = None,
    *,
    theme: str | None = None,
) -> StudioPalette:
    global _CURRENT
    if palette is None:
        key = (theme or "").strip().lower()
        if key == "light":
            palette = LIGHT
        elif key == "dark":
            palette = DARK
        else:
            palette = palette_for_app(app)
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    font = QFont(system_font_family())
    font.setPixelSize(13)
    app.setFont(font)
    app.setPalette(_qt_palette(palette))
    app.setStyleSheet(qss(palette))
    _CURRENT = palette
    return palette
