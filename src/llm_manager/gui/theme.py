"""Theming: Claude-Desktop-calm shell, dark-academia skin.

Modern Mac-native chrome — soft borders, rounded corners, inset nav pills,
generous whitespace, tinted (not loud) selection — dressed in an espresso /
parchment / antique-brass palette with a bookish serif carrying the hierarchy.

One place owns every colour and font. Pages style via object names, never hex.
Palette follows the macOS appearance unless overridden from the sidebar footer.
"""
from __future__ import annotations

# --- Type -------------------------------------------------------------------

SERIF = '"Hoefler Text", "New York", "Iowan Old Style", Palatino, Georgia, serif'
SANS = '-apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif'
MONO = '"SF Mono", "JetBrains Mono", Menlo, Monaco, monospace'

# --- Palettes -------------------------------------------------------------------

DARK: dict[str, str] = {
    "app": "#151719",          # window background — near-black, faintly cool neutral
    "sidebar": "#111315",      # sidebar, a shade deeper
    "surface": "#1C1F21",      # cards, composer, panels
    "raised": "#25292B",       # hover, nested rows
    "border": "#2A2E30",       # soft hairline, used sparingly
    "border_strong": "#383D3F",  # emphasis border / focus-adjacent
    "text": "#E7E8E6",         # primary — near-white, faint warmth
    "text_dim": "#9AA09C",     # secondary
    "text_faint": "#6C726E",   # labels, captions, meta
    "accent": "#5E8C6E",       # muted forest green
    "accent_hover": "#71A281",
    "accent_press": "#4A7057",
    "accent_tint": "#1B2420",   # green at low alpha over app — nav active
    "accent_tint_hi": "#20302A",  # slightly stronger — user message
    "danger": "#B4675E",
    "success": "#71A281",
    "on_accent": "#0F1512",    # text on a green fill
    "pygments": "monokai",
}

LIGHT: dict[str, str] = {
    "app": "#F4F5F3",
    "sidebar": "#ECEEEB",
    "surface": "#FBFBFA",
    "raised": "#E5E8E4",
    "border": "#DDE0DC",
    "border_strong": "#C3C8C3",
    "text": "#1E211F",
    "text_dim": "#585D58",
    "text_faint": "#7C817C",
    "accent": "#3C6E4E",
    "accent_hover": "#4A8360",
    "accent_press": "#2F5940",
    "accent_tint": "#E2ECE4",
    "accent_tint_hi": "#D5E3D8",
    "danger": "#9E5148",
    "success": "#3C6E4E",
    "on_accent": "#F7FAF7",
    "pygments": "friendly",
}

_override: str | None = None  # "dark" | "light" | None (follow system)


# --- Stylesheet ---------------------------------------------------------------


def build_stylesheet(p: dict[str, str]) -> str:
    return f"""
* {{
    font-family: {SANS};
    font-size: 13px;
    color: {p['text']};
}}
QMainWindow, QDialog {{ background-color: {p['app']}; }}
QWidget {{ background-color: transparent; }}
QMainWindow > QWidget {{ background-color: {p['app']}; }}

/* ---- Sidebar ---- */
#sidebar {{ background-color: {p['sidebar']}; border: none; min-width: 224px; max-width: 224px; }}
#wordmark {{
    font-family: {SERIF};
    font-size: 20px;
    color: {p['text']};
    padding: 24px 20px 2px 20px;
}}
#wordmarkRule {{ background-color: {p['accent']}; max-width: 28px; max-height: 2px; margin: 8px 20px; }}
#wordmarkSub {{
    font-family: {SERIF};
    font-style: italic;
    font-size: 12px;
    color: {p['text_faint']};
    padding: 0 20px 18px 20px;
}}
QToolButton#navGroup {{
    background: transparent;
    border: none;
    color: {p['text_faint']};
    font-size: 10px;
    font-weight: 700;
    text-align: left;
    padding: 14px 20px 4px 20px;
}}
QToolButton#navGroup:hover {{ color: {p['text_dim']}; }}
#navBtn {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 7px;
    text-align: left;
    padding: 7px 12px;
    margin: 1px 10px;
    color: {p['text_dim']};
    font-size: 13px;
    font-weight: 500;
}}
#navBtn:hover {{ background-color: {p['raised']}; color: {p['text']}; }}
#navBtn:checked {{
    background-color: {p['accent_tint']};
    border-left: 2px solid {p['accent']};
    color: {p['text']};
    font-weight: 600;
}}
#sidebarFooter {{ border-top: 1px solid {p['border']}; }}
#versionLabel {{ font-family: {SERIF}; font-style: italic; color: {p['text_faint']}; }}

/* ---- Page header ---- */
#pageHeader {{ background-color: {p['app']}; }}
#pageEyebrow {{
    font-family: {SANS};
    font-size: 10px;
    font-weight: 700;
    color: {p['text_faint']};
}}
#pageTitle {{ font-family: {SERIF}; font-size: 27px; color: {p['text']}; }}
#runningHead {{ font-family: {SANS}; font-size: 12px; color: {p['text_faint']}; }}
#headerRule {{ background-color: {p['border']}; max-height: 1px; }}
#sectionLabel {{ font-family: {SANS}; font-size: 11px; font-weight: 700; color: {p['text_faint']}; }}
#fieldLabel {{ color: {p['text_dim']}; }}
#cardTitle {{ font-family: {SERIF}; font-size: 15px; color: {p['text']}; }}
#mono {{ font-family: {MONO}; font-size: 12px; color: {p['text_dim']}; }}
#emptyState {{ color: {p['text_faint']}; font-family: {SERIF}; font-size: 15px; }}
#subtitle, #detailLabel {{ color: {p['text_faint']}; }}

/* ---- Card (lightweight panel; replaces bordered QGroupBox) ---- */
#card {{ background-color: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px; }}

/* ---- Buttons ---- */
QPushButton {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 6px 13px;
    color: {p['text']};
}}
QPushButton:hover {{ background-color: {p['raised']}; border-color: {p['border_strong']}; }}
QPushButton:pressed {{ background-color: {p['raised']}; }}
QPushButton:disabled {{ color: {p['text_faint']}; background-color: transparent; border-color: {p['border']}; }}
QPushButton#primary {{
    background-color: {p['accent']};
    border: none;
    color: {p['on_accent']};
    font-weight: 600;
    padding: 7px 15px;
}}
QPushButton#primary:hover {{ background-color: {p['accent_hover']}; }}
QPushButton#primary:pressed {{ background-color: {p['accent_press']}; }}
QPushButton#primary:disabled {{ background-color: {p['raised']}; color: {p['text_faint']}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {p['border']}; color: {p['danger']}; }}
QPushButton#danger:hover {{ background-color: {p['danger']}; border-color: {p['danger']}; color: {p['on_accent']}; }}
QPushButton#quiet {{ background: transparent; border: none; color: {p['text_dim']}; padding: 5px 9px; }}
QPushButton#quiet:hover {{ color: {p['accent']}; background: transparent; }}

/* ---- Segmented control ---- */
#segmentTrack {{ background-color: {p['raised']}; border-radius: 9px; }}
QPushButton#segment {{
    background: transparent;
    border: none;
    border-radius: 7px;
    padding: 5px 14px;
    margin: 2px;
    color: {p['text_dim']};
    font-weight: 500;
}}
QPushButton#segment:hover {{ color: {p['text']}; }}
QPushButton#segment:checked {{ background-color: {p['surface']}; color: {p['text']}; font-weight: 600; }}

/* ---- Status badge ---- */
#statusBadge {{ font-weight: 600; }}
#statusBadge[state="running"] {{ color: {p['success']}; }}
#statusBadge[state="stopped"] {{ color: {p['text_faint']}; }}

/* ---- Inputs ---- */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 10px;
    color: {p['text']};
    padding: 7px 9px;
    selection-background-color: {p['accent']};
    selection-color: {p['on_accent']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {p['accent']}; }}
QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 5px 9px;
    color: {p['text']};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {p['accent_tint']};
    selection-color: {p['text']};
    outline: none;
}}
QTextEdit#chatDisplay {{ background-color: {p['app']}; border: none; border-radius: 0; padding: 0; }}
#paneTitle {{ font-family: {SERIF}; font-size: 14px; color: {p['text']}; padding: 2px 2px 0 2px; }}
#paneStat {{ font-family: {MONO}; font-size: 11px; color: {p['text_faint']}; padding: 0 2px 4px 2px; }}
#chatStrip {{ border-bottom: 1px solid {p['border']}; }}
QPushButton#toggleBtn {{
    background: transparent;
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 6px 12px;
    color: {p['text_dim']};
}}
QPushButton#toggleBtn:hover {{ color: {p['text']}; border-color: {p['border_strong']}; }}
QPushButton#toggleBtn:checked {{
    background-color: {p['accent_tint']};
    border-color: {p['accent']};
    color: {p['text']};
    font-weight: 600;
}}
#presetDesc {{ color: {p['text_faint']}; font-size: 12px; padding: 2px 0 4px 0; }}
QPushButton#discloseBtn {{
    background: transparent;
    border: none;
    text-align: left;
    padding: 4px 0;
    color: {p['text_dim']};
    font-weight: 600;
}}
QPushButton#discloseBtn:hover {{ color: {p['text']}; }}
#convRail, #railStrip {{ background-color: {p['sidebar']}; border-right: 1px solid {p['border']}; }}
QToolButton#railNew {{
    background-color: {p['accent']};
    color: {p['on_accent']};
    border: none;
    border-radius: 8px;
    padding: 7px 10px;
    font-weight: 600;
}}
QToolButton#railNew:hover {{ background-color: {p['accent_hover']}; }}
QToolButton#railNew::menu-button {{ border: none; width: 16px; }}
QToolButton#railCollapse {{
    background: transparent;
    border: 1px solid {p['border']};
    border-radius: 8px;
    color: {p['text_dim']};
    padding: 4px 8px;
}}
QToolButton#railCollapse:hover {{ color: {p['text']}; border-color: {p['border_strong']}; }}
QTreeWidget#convTree {{
    background: transparent;
    border: none;
    outline: none;
    show-decoration-selected: 1;
    selection-background-color: {p["accent"]};
    selection-color: {p['text']};
}}
QTreeWidget#convTree::item {{ padding: 7px 4px; border-radius: 8px; color: {p['text_dim']}; }}
QTreeWidget#convTree::item:hover {{ background-color: {p['raised']}; color: {p['text']}; }}
QTreeWidget#convTree::item:selected {{ background-color: {p['accent']}; color: {p['on_accent']}; }}
QTreeWidget#convTree::branch {{ background: transparent; }}
QWidget#composer {{ background-color: {p['surface']}; border: 1px solid {p['border']}; border-radius: 14px; }}
QWidget#composer QTextEdit {{ background: transparent; border: none; padding: 2px; }}
QWidget#toolbar {{ background-color: transparent; border: none; }}
QWidget#chatSidebar {{ background-color: {p['sidebar']}; border: none; border-left: 1px solid {p['border']}; }}
QSplitter#chatArea::handle {{ background-color: {p['border']}; }}

/* ---- Tables ---- */
QTableWidget, QTableView {{
    background-color: transparent;
    alternate-background-color: transparent;
    gridline-color: transparent;
    border: none;
    outline: none;
}}
QTableWidget::item {{ padding: 5px 6px; border-bottom: 1px solid {p['border']}; }}
QTableWidget::item:selected, QTableView::item:selected {{ background-color: {p['accent_tint']}; color: {p['text']}; }}
QTableWidget::item:hover {{ background-color: {p['raised']}; }}
QHeaderView::section {{
    background-color: transparent;
    color: {p['text_faint']};
    border: none;
    border-bottom: 1px solid {p['border_strong']};
    padding: 6px 6px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* ---- Tabs ---- */
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent;
    color: {p['text_dim']};
    padding: 7px 16px;
    border: none;
    margin-right: 2px;
    border-radius: 8px;
}}
QTabBar::tab:selected {{ color: {p['text']}; background: {p['raised']}; }}
QTabBar::tab:hover:!selected {{ color: {p['text']}; }}
QTabBar::close-button {{ subcontrol-position: right; }}

/* ---- Scrollbars ---- */
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['border_strong']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {p['text_faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p['border_strong']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- Sliders ---- */
QSlider::groove:horizontal {{ background: {p['raised']}; height: 4px; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p['accent']};
    width: 15px; height: 15px;
    border-radius: 8px;
    margin: -6px 0;
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}

/* ---- Progress ---- */
QProgressBar {{
    background: {p['raised']};
    border: none;
    border-radius: 5px;
    height: 8px;
    text-align: center;
    color: {p['text_faint']};
}}
QProgressBar::chunk {{ background-color: {p['accent']}; border-radius: 5px; }}

/* ---- Group box (soft card) ---- */
QGroupBox {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 12px;
    margin-top: 24px;
    padding: 18px 14px 14px 14px;
    color: {p['text_dim']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 2px 2px;
    font-family: {SERIF};
    font-size: 15px;
    color: {p['text']};
}}

/* ---- Lists ---- */
QListWidget {{ background-color: transparent; border: none; outline: none; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 8px; margin: 1px 0; }}
QListWidget::item:hover {{ background-color: {p['raised']}; }}
QListWidget::item:selected {{ background-color: {p['accent_tint']}; color: {p['text']}; }}

QSplitter::handle {{ background: transparent; }}

/* ---- Menu ---- */
QMenu {{ background-color: {p['surface']}; border: 1px solid {p['border']}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background-color: {p['accent_tint']}; color: {p['text']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 5px 8px; }}

QToolTip {{
    background-color: {p['surface']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 6px;
    padding: 5px 7px;
}}
"""


def _pygments_css(style: str) -> str:
    try:
        from pygments.formatters import HtmlFormatter
        from pygments.util import ClassNotFound
    except ImportError:
        return ""
    try:
        return HtmlFormatter(style=style).get_style_defs(".codehilite")
    except ClassNotFound:
        return ""


def chat_css(p: dict[str, str]) -> str:
    """The <style> block injected into the chat transcript QTextEdit."""
    return f"""
<style>
{_pygments_css(p.get('pygments', 'monokai'))}
body {{ font-family: {SANS}; font-size: 14px; line-height: 170%; color: {p['text']};
       background: {p['app']}; padding: 14px 10px; }}
.msg {{ margin: 0 0 28px 0; }}
.user-tbl td {{ background: {p['accent_tint_hi']}; line-height: 145%; }}
pre {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 8px;
      padding: 12px; overflow-x: auto; }}
code {{ font-family: {MONO}; font-size: 12.5px; }}
table {{ border-collapse: collapse; width: 100%; margin: 6px 0; }}
th, td {{ border: 1px solid {p['border']}; padding: 7px 11px; text-align: left; }}
th {{ background: {p['surface']}; }}
a {{ color: {p['accent']}; }}
.streaming-cursor {{ display: inline-block; width: 7px; height: 15px;
      background: {p['accent']}; vertical-align: text-bottom; }}
</style>
"""


# --- Appearance wiring -------------------------------------------------------


def current_palette(app) -> dict[str, str]:
    if _override == "dark":
        return DARK
    if _override == "light":
        return LIGHT
    from PySide6.QtCore import Qt

    if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Light:
        return LIGHT
    return DARK


def apply(app) -> None:
    app.setStyleSheet(build_stylesheet(current_palette(app)))


def install(app) -> None:
    apply(app)
    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):
        hints.colorSchemeChanged.connect(lambda *_: apply(app))


def set_override(app, mode: str | None) -> None:
    """mode: 'dark', 'light', or None to follow the system."""
    global _override
    _override = mode
    apply(app)
