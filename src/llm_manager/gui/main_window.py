from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from llm_manager.gui import theme
from llm_manager.gui.pages.chat_page import ChatPage
from llm_manager.gui.pages.models_page import ModelsPage
from llm_manager.gui.pages.server_page import ServerPage
from llm_manager.gui.pages.storage_page import StoragePage
from llm_manager.gui.pages.templates_page import TemplatesPage
from llm_manager.gui.widgets import SegmentedControl, svg_icon

# (group label, [(page label, page index, icon name), ...])
_NAV_GROUPS = [
    ("Workspace", [("Chat", 0, "chat")]),
    ("Library", [("Models", 1, "cube"), ("Storage", 2, "disk"), ("Templates", 3, "page")]),
    ("Connection", [("API Server", 4, "plug")]),
]
_PAGE_COUNT = 5
_NAV_ICON_COLOR = "#8b918b"

_SHORTCUTS = [
    ("⌘N", "New conversation"),
    ("⌘1 – ⌘5", "Jump to a section"),
    ("⌘L", "Focus the message box"),
    ("⌘F", "Filter the model list"),
    ("⌘↩", "Send the message"),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        for keys, what in _SHORTCUTS:
            k = QLabel(keys)
            k.setObjectName("mono")
            form.addRow(k, QLabel(what))
        layout.addLayout(form)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close_btn)
        layout.addLayout(row)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LLM Manager")
        self.setMinimumSize(1024, 680)
        self.resize(1280, 800)
        self._build_ui()
        self._install_shortcuts()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._chat_page = ChatPage()
        self._models_page = ModelsPage()
        self._storage_page = StoragePage()
        self._templates_page = TemplatesPage()
        self._server_page = ServerPage()
        for page in (
            self._chat_page,
            self._models_page,
            self._storage_page,
            self._templates_page,
            self._server_page,
        ):
            self._stack.addWidget(page)
        root.addWidget(self._stack, 1)

        self._models_page.chat_with_model.connect(self._navigate_to_chat)
        self._templates_page.use_in_chat.connect(self._apply_template)

        self._switch_page(0)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        wordmark = QLabel("LLM Manager")
        wordmark.setObjectName("wordmark")
        layout.addWidget(wordmark)
        rule = QFrame()
        rule.setObjectName("wordmarkRule")
        rule.setFixedHeight(2)
        layout.addWidget(rule)
        sub = QLabel("ex libris · local models")
        sub.setObjectName("wordmarkSub")
        layout.addWidget(sub)

        self._settings = QSettings("llm-manager", "LLM Manager")
        self._nav_buttons: list[QPushButton | None] = [None] * _PAGE_COUNT
        for group, items in _NAV_GROUPS:
            collapsed = self._settings.value(f"nav/{group}/collapsed", False, type=bool)
            head = QToolButton()
            head.setObjectName("navGroup")
            head.setCheckable(True)
            head.setChecked(not collapsed)
            head.setCursor(Qt.CursorShape.PointingHandCursor)
            head.setText(f"{'▾' if not collapsed else '▸'}  {group.upper()}")
            layout.addWidget(head)

            group_items = []
            for label, idx, icon in items:
                btn = QPushButton(f"  {label}")
                btn.setObjectName("navBtn")
                btn.setCheckable(True)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.setIcon(svg_icon(icon, _NAV_ICON_COLOR))
                btn.setIconSize(QSize(17, 17))
                btn.clicked.connect(lambda _c, i=idx: self._switch_page(i))
                btn.setVisible(not collapsed)
                layout.addWidget(btn)
                self._nav_buttons[idx] = btn
                group_items.append(btn)

            head.toggled.connect(
                lambda shown, g=group, h=head, its=group_items: self._toggle_group(shown, g, h, its)
            )

        layout.addStretch()

        footer = QWidget()
        footer.setObjectName("sidebarFooter")
        fl = QVBoxLayout(footer)
        fl.setContentsMargins(14, 12, 14, 0)
        fl.setSpacing(8)
        self._theme_control = SegmentedControl(["Auto", "Light", "Dark"])
        self._theme_control.currentChanged.connect(self._on_theme_changed)
        fl.addWidget(self._theme_control)
        row = QHBoxLayout()
        shortcuts_btn = QPushButton("Shortcuts")
        shortcuts_btn.setObjectName("quiet")
        shortcuts_btn.clicked.connect(lambda: ShortcutsDialog(self).exec())
        row.addWidget(shortcuts_btn)
        row.addStretch()
        version = QLabel("v0.1.0")
        version.setObjectName("versionLabel")
        row.addWidget(version)
        fl.addLayout(row)
        layout.addWidget(footer)

        return sidebar

    def _install_shortcuts(self):
        def bind(seq, slot):
            QShortcut(QKeySequence(seq), self, activated=slot)

        bind("Ctrl+N", self._chat_page.new_conversation)
        bind("Ctrl+L", self._chat_page.focus_input)
        bind("Ctrl+F", self._focus_search)
        for i in range(_PAGE_COUNT):
            bind(f"Ctrl+{i + 1}", lambda i=i: self._switch_page(i))

    def _toggle_group(self, shown: bool, group: str, head: QToolButton, items: list):
        head.setText(f"{'▾' if shown else '▸'}  {group.upper()}")
        for btn in items:
            btn.setVisible(shown)
        self._settings.setValue(f"nav/{group}/collapsed", not shown)

    def _on_theme_changed(self, label: str):
        app = QApplication.instance()
        theme.set_override(app, {"Auto": None, "Light": "light", "Dark": "dark"}[label])
        self._chat_page.retheme()

    def _switch_page(self, index: int):
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._nav_buttons):
            if btn is not None:
                btn.setChecked(i == index)
        if index == 1:
            self._models_page.refresh()
        elif index == 2:
            self._storage_page.refresh()
        elif index == 3:
            self._templates_page.refresh()

    def _focus_search(self):
        if self._stack.currentIndex() == 1:
            self._models_page.focus_search()

    def _navigate_to_chat(self, model_name: str, backend: str):
        self._switch_page(0)
        self._chat_page.refresh_models()
        self._chat_page.select_model(model_name, backend)

    def _apply_template(self, system_prompt: str, user_prompt: str):
        self._switch_page(0)
        view = self._chat_page.current_view()
        view._system_prompt.setPlainText(system_prompt)
        view._input.setPlainText(user_prompt)
