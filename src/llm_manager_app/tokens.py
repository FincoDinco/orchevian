"""Studio visual tokens: named colors and a small QSS string."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

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
    radius_control: int = 12
    radius_composer: int = 26


# DESIGN.md Visual System — Studio
DARK = StudioPalette(
    canvas="#181818",
    elevated="#282828",
    text="#E8E8E8",
    secondary="#969696",
    accent="#6E9EE8",
    danger="#FF453A",
    selection="rgba(255, 255, 255, 20)",
    separator="rgba(255, 255, 255, 16)",
    sidebar="#232323",
)

LIGHT = StudioPalette(
    canvas="#FAFAFC",
    elevated="#FFFFFF",
    text="#1D1D1F",
    secondary="#636366",
    accent="#0066CC",
    danger="#FF3B30",
    selection="rgba(68, 103, 196, 24)",
    separator="rgba(0, 0, 0, 20)",
    sidebar="#EFEFF2",
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
    chevron = (Path(__file__).parent / "assets" / "chevron-down.svg").as_posix()
    return f"""
    QWidget#privateChatBanner {{
        background: {palette.elevated}; border: 2px solid {palette.accent};
        border-radius: 12px;
    }}
    QMainWindow, QDialog, QWidget#shell {{
        background-color: {palette.canvas}; color: {palette.text}; font-family: "{family}";
    }}
    QWidget {{ color: {palette.text}; font-family: "{family}"; }}
    QWidget#sidebar, QTreeView#sidebarNav {{
        background-color: {palette.sidebar}; color: {palette.text}; border: none; outline: none;
    }}
    QScrollArea {{ border: none; background: {palette.canvas}; }}
    QToolBar#workspaceToolbar {{ background: {palette.canvas}; border: none;
        spacing: 8px; padding: 4px 12px; }}
    QToolBar#workspaceToolbar QToolButton {{ background: transparent; border: none;
        border-radius: {r}px; padding: 5px; min-width: 24px; min-height: 24px; }}
    QToolBar#workspaceToolbar QToolButton:hover,
    QToolBar#workspaceToolbar QToolButton:checked {{ background: {palette.selection}; }}
    QFrame#downloadsPopover {{ background: {palette.elevated};
        border: 1px solid {palette.separator}; border-radius: 16px; }}
    QFrame#downloadsPopover QScrollArea, QFrame#downloadsPopover QScrollArea > QWidget > QWidget {{
        background: {palette.elevated}; }}
    QWidget#downloadCard {{ background: transparent;
        border: none; border-bottom: 1px solid {palette.separator}; }}
    QLabel#downloadDetails {{ color: {palette.secondary}; padding: 4px 26px; }}
    QToolButton#modelDetailsDisclosure {{ color: {palette.secondary}; border: none;
        background: transparent; padding: 6px 0; }}
    QProgressBar {{ border: none; background: {palette.separator};
        border-radius: 3px; min-height: 5px; max-height: 5px; }}
    QProgressBar::chunk {{ background: {palette.accent}; border-radius: 3px; }}
    QWidget#listPane, QListView#conversationView, QListWidget#modelsList,
    QListWidget#discoveryResults,
    QWidget#detailPane, QWidget#transcript, QWidget#chatBody {{
        background-color: {palette.canvas}; color: {palette.text}; border: none; outline: none;
    }}
    QTreeView#sidebarNav::item {{
        padding: 6px 10px; margin: 1px 8px; border-radius: {r}px; min-height: 22px;
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
        border: 1px solid {palette.separator}; border-radius: {r}px; padding: 6px 12px;
    }}
    QPushButton:hover {{ background-color: {palette.selection}; }}
    QPushButton:disabled {{ color: {palette.secondary}; }}
    QToolButton {{ border: none; border-radius: {r}px; padding: 6px; background: transparent; }}
    QToolButton:hover {{ background: {palette.selection}; }}
    QPlainTextEdit {{ background: {palette.elevated}; border: none;
        border-radius: {r}px; padding: 10px; }}
    QPushButton#newChatButton, QPushButton#openModelsButton,
    QPushButton#regenerateButton, QPushButton#inspectorToggle, QPushButton#inspectorTab {{
        background-color: transparent; color: {palette.secondary}; border: none; padding: 6px 8px;
    }}
    QPushButton#inspectorToggle:checked, QPushButton#inspectorTab:checked {{
        color: {palette.text};
    }}
    QLabel#listEmpty, QLabel#chatEmpty, QLabel#modelEmpty,
    QLabel#lastTurnLabel, QLabel#settingsHint, QLabel#inspectorSection {{
        color: {palette.secondary}; background: transparent;
    }}
    QLabel#lastTurnLabel {{ font-family: "{mono}"; }}
    QToolButton#sidebarCollapse, QToolButton#inspectorCollapse, QToolButton#newProjectButton,
    QToolButton#sidebarSearch {{
        background: transparent; color: {palette.secondary}; border: none; padding: 4px;
    }}
    QToolButton#thinkingDisclosure, QToolButton#advancedDisclosure {{
        background: transparent; color: {palette.secondary}; border: none;
        padding: 6px 10px; border-radius: {r}px;
    }}
    QToolButton#thinkingDisclosure:hover, QToolButton#advancedDisclosure:hover {{
        background: {palette.selection}; color: {palette.text};
    }}
    QPlainTextEdit#thinkingBody {{ background: {palette.elevated}; color: {palette.secondary};
        border: none; border-radius: {r}px; padding: 10px; }}
    QToolButton#sidebarCollapse:hover, QToolButton#inspectorCollapse:hover {{
        background-color: {palette.selection}; color: {palette.text};
    }}
    QToolButton#modelPicker, QToolButton#projectHomeModelPicker {{
        background: transparent; color: {palette.secondary}; border: none;
        padding: 6px 10px; border-radius: {r}px;
    }}
    QToolButton#modelPicker:hover, QToolButton#projectHomeModelPicker:hover,
    QToolButton#modelPicker:pressed, QToolButton#projectHomeModelPicker:pressed {{
        background-color: {palette.selection};
    }}
    QToolButton#modelPicker::menu-indicator, QToolButton#projectHomeModelPicker::menu-indicator {{
        subcontrol-position: right center; right: 1px; }}
    QTextBrowser#transcriptHistory, QPlainTextEdit#transcriptStream {{
        background-color: {palette.canvas}; color: {palette.text}; border: none;
        font-size: 16px;
    }}
    QPlainTextEdit#composerEdit, QPlainTextEdit#projectComposerEdit {{
        background-color: transparent; color: {palette.text};
        border: none; padding: 4px; font-size: 15px;
    }}
    QPushButton#sendButton, QPushButton#projectSendButton {{
        background-color: {palette.accent}; color: {palette.canvas};
        border: none; border-radius: 17px; padding: 0; font-size: 20px; font-weight: 600;
    }}
    QPushButton#sendButton:hover, QPushButton#projectSendButton:hover {{
        background-color: {palette.accent}; }}
    QPushButton#sendButton:disabled, QPushButton#projectSendButton:disabled {{
        background-color: {palette.selection}; color: {palette.secondary};
    }}
    QPushButton#sendButton[mode="stop"], QPushButton#projectSendButton[mode="stop"] {{
        background-color: {palette.danger}; color: #FFFFFF; border-radius: 6px;
    }}
    QLabel#chatBanner, QLabel#settingsError, QLabel#modelsError, QLabel#modelsBanner {{
        color: {palette.danger}; background-color: {palette.elevated};
        border-radius: {r}px; padding: 8px 10px;
    }}
    QWidget#inspector {{
        background: {palette.elevated}; color: {palette.text}; border: none; border-radius: 16px;
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
    QPushButton#chatButton {{
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
    QTabBar {{ background: {palette.sidebar}; border-radius: {r}px; }}
    QTabBar::tab {{
        background: transparent; color: {palette.secondary}; padding: 6px 16px;
        border: 1px solid transparent; border-radius: 10px; margin: 3px;
    }}
    QTabBar::tab:selected {{ color: {palette.text}; background: {palette.elevated};
        border: 1px solid {palette.separator}; }}
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
    QComboBox::drop-down {{ border: none; width: 28px; }}
    QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
    QComboBox QAbstractItemView {{
        background-color: {palette.elevated}; color: {palette.text};
        selection-background-color: {palette.selection}; border: none; border-radius: {r}px;
    }}
    QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 8px; }}
    QCheckBox {{ color: {palette.text}; spacing: 8px; }}
    QToolTip {{
        background-color: {palette.elevated}; color: {palette.text};
        border: none; padding: 4px 8px;
    }}
    QWidget#chatToolbar {{ background: transparent; border: none; }}
    QWidget#sidebar QWidget#listPane, QWidget#sidebar QListView#conversationView {{
        background: {palette.sidebar};
    }}
    QLabel#brandTitle {{ font-size: 19px; font-weight: 600; letter-spacing: -0.3px; }}
    QLabel#sidebarSection {{ color: {palette.secondary}; padding: 0 14px 2px;
        font-size: 14px; }}
    QLabel#sidebarStatus {{ color: {palette.secondary}; font-size: 12px; }}
    QWidget#sidebarFooter {{ border-top: 1px solid {palette.separator}; }}
    QTreeView#sidebarNav, QListView#conversationView {{ font-size: 14px; }}
    QLabel#eyebrow {{ color: {palette.secondary}; font-size: 10px;
        font-weight: 600; letter-spacing: 1px; }}
    QLabel#chatTitle {{ color: {palette.secondary}; font-size: 14px; font-weight: 400; }}
    QLabel#pageTitle {{ font-size: 18px; font-weight: 600; letter-spacing: -0.2px; }}
    QLabel#modelTitle {{ font-size: 20px; font-weight: 600; letter-spacing: -0.3px; }}
    QLabel#pageSubtitle, QLabel#welcomeSubtitle {{ color: {palette.secondary}; font-size: 13px; }}
    QLabel#welcomeTitle {{ font-size: 28px; font-weight: 600; letter-spacing: -0.8px; }}
    QLabel#chatEmpty {{ font-size: 14px; }}
    QWidget#projectGuidanceCard {{ background: {palette.elevated};
        border: 1px solid {palette.separator}; border-radius: {r}px; }}
    QListWidget#projectConversations {{ background: transparent; border: none; }}
    QListWidget#projectConversations::item {{ padding: 8px 12px; border-radius: {r}px; }}
    QListWidget#projectConversations::item:hover {{ background: {palette.selection}; }}
    QWidget#composer, QWidget#projectComposer {{
        background: {palette.elevated}; border: 1px solid {palette.separator};
        border-radius: {palette.radius_composer}px; }}
    QPushButton#sidebarNewChat {{ background: transparent; border: none;
        text-align: left; padding: 8px 10px; font-size: 14px; }}
    QPushButton#sidebarNewChat:hover {{ background: {palette.selection}; }}
    QPushButton#sidebarSettings {{ background: transparent; border: none; padding: 6px; }}
    QPushButton#welcomeNewChat, QPushButton#primaryButton {{ background: {palette.accent};
        color: {palette.canvas}; border: none; }}
    QPushButton#primaryButton:disabled {{ background: {palette.selection};
        color: {palette.secondary}; }}
    QPushButton#starterButton {{ text-align: left; padding: 12px 16px; background: transparent; }}
    QPushButton#starterButton:hover {{ background: {palette.elevated};
        border-color: {palette.accent}; }}
    QLabel#catalogEmpty {{ color: {palette.secondary}; padding: 8px; }}
    QWidget#modelFacts {{ background: {palette.elevated}; border-radius: 16px; }}
    QLabel#factValue {{ font-size: 18px; font-weight: 600; }}
    QLabel#modelsDetailBody {{ color: {palette.secondary}; line-height: 1.6; }}
    QLineEdit:focus, QPushButton:focus, QToolButton:focus, QComboBox:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {palette.accent}; }}
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
    styles = {name.lower(): name for name in QStyleFactory.keys()}
    preferred = "macintosh" if sys.platform == "darwin" else "fusion"
    if preferred in styles:
        app.setStyle(styles[preferred])
    font = QFont(system_font_family())
    font.setPixelSize(13)
    app.setFont(font)
    app.setPalette(_qt_palette(palette))
    app.setStyleSheet(qss(palette))
    _CURRENT = palette
    return palette
