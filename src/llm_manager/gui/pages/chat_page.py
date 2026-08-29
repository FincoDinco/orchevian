import html
import time
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llm_manager import database as db
from llm_manager.backends import get_backend, list_all_models
from llm_manager.gui import theme
from llm_manager.gui.widgets import Disclosure, PageHeader, SegmentedControl
from llm_manager.models import LocalModel

try:
    import markdown as _markdown  # noqa: F401

    _HAS_MARKDOWN = True
except ImportError:
    _HAS_MARKDOWN = False

_PRESETS = {
    "Precise": (0.2, 0.8, "Sticks close to the likeliest answer. Good for facts and code."),
    "Balanced": (0.7, 0.9, "A sensible mix of reliability and variety. The usual choice."),
    "Creative": (1.1, 0.98, "Roams further for ideas and phrasing. Good for brainstorming."),
}


def _render_markdown(text: str) -> str:
    if not _HAS_MARKDOWN:
        return f"<pre>{html.escape(text)}</pre>"
    import markdown as md_lib

    return md_lib.markdown(
        text,
        extensions=["fenced_code", "codehilite", "tables", "nl2br"],
        extension_configs={"codehilite": {"css_class": "codehilite", "guess_lang": True}},
    )


def _palette() -> dict:
    from PySide6.QtWidgets import QApplication

    return theme.current_palette(QApplication.instance())


def _chat_css() -> str:
    return theme.chat_css(_palette())


def _bubble(role: str, content_html: str, cursor: bool = False) -> str:
    tail = '<span class="streaming-cursor"></span>' if cursor else ""
    if role == "user":
        tint = _palette()["accent_tint_hi"]
        return (
            '<div class="msg">'
            f'<table class="user-tbl" width="70%" cellpadding="11" cellspacing="0">'
            f'<tr><td bgcolor="{tint}">{content_html}{tail}</td></tr></table></div>'
        )
    return f'<div class="msg">{content_html}{tail}</div>'


def _empty_doc(has_model: bool) -> str:
    p = _palette()
    line = (
        "Type a message below to begin."
        if has_model
        else "Choose a model in the bar above, then say hello."
    )
    return (
        _chat_css()
        + f'<body><div style="text-align:center;color:{p["text_faint"]};'
        f'font-family:{theme.SERIF};font-size:15px;padding-top:80px;">'
        f"New conversation<br><span style=\"font-size:13px;\">{line}</span></div></body>"
    )


def _relative(dt: datetime) -> str:
    secs = (datetime.now() - dt).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    if secs < 7 * 86400:
        return f"{int(secs // 86400)}d ago"
    return dt.strftime("%b %d")


# =====================================================================
# Conversation rail
# =====================================================================


class ProjectDialog(QDialog):
    """Create or edit a project (a folder of chats + a custom assistant)."""

    def __init__(self, models: list[LocalModel], project=None, parent=None):
        super().__init__(parent)
        self._models = models
        self.setWindowTitle("Edit project" if project else "New project")
        self.setMinimumWidth(460)
        form = QVBoxLayout(self)
        form.setSpacing(10)

        form.addWidget(QLabel("Name"))
        self._name = QLineEdit(project.name if project else "")
        self._name.setPlaceholderText("e.g. CS101 Tutor")
        form.addWidget(self._name)

        form.addWidget(QLabel("Instructions"))
        self._instructions = QTextEdit(project.instructions if project else "")
        self._instructions.setPlaceholderText(
            "How this assistant should behave. Every new chat in the project starts with these."
        )
        self._instructions.setMinimumHeight(120)
        form.addWidget(self._instructions)

        form.addWidget(QLabel("Default model"))
        self._model = QComboBox()
        self._model.addItem("— none —", None)
        for m in models:
            self._model.addItem(f"{m.name}  ·  {m.display_backend}", (m.name, m.backend))
        if project and project.model_name:
            for i in range(1, self._model.count()):
                if self._model.itemData(i) == (project.model_name, project.backend):
                    self._model.setCurrentIndex(i)
                    break
        form.addWidget(self._model)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)
        form.addWidget(bb)

    def _try_accept(self):
        if not self._name.text().strip():
            QMessageBox.warning(self, "Name required", "Give the project a name.")
            return
        self.accept()

    def values(self) -> dict:
        md = self._model.currentData()
        return {
            "name": self._name.text().strip(),
            "instructions": self._instructions.toPlainText().strip(),
            "model_name": md[0] if md else "",
            "backend": md[1] if md else "",
        }


class ConversationRail(QWidget):
    selected = Signal(int)
    # new_requested(project_id | -1 for "no project")
    new_requested = Signal(int)

    _ROLE = Qt.ItemDataRole.UserRole  # value: ("conv", id) | ("project", id)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("convRail")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 8, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._new_btn = QToolButton()
        self._new_btn.setText("＋  New")
        self._new_btn.setObjectName("railNew")
        self._new_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._new_btn.clicked.connect(lambda: self.new_requested.emit(self._active_project_id()))
        nm = QMenu(self._new_btn)
        nm.addAction("New chat").triggered.connect(lambda: self.new_requested.emit(-1))
        nm.addAction("New chat in current project").triggered.connect(
            lambda: self.new_requested.emit(self._active_project_id())
        )
        nm.addSeparator()
        nm.addAction("New project…").triggered.connect(self._new_project)
        self._new_btn.setMenu(nm)
        head.addWidget(self._new_btn, 1)
        self._collapse_btn = QToolButton()
        self._collapse_btn.setText("‹")
        self._collapse_btn.setObjectName("railCollapse")
        self._collapse_btn.setToolTip("Hide the conversation list")
        head.addWidget(self._collapse_btn)
        layout.addLayout(head)

        self._tree = QTreeWidget()
        self._tree.setObjectName("convTree")
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._menu)
        self._tree.itemDoubleClicked.connect(self._on_double)
        self._tree.currentItemChanged.connect(self._on_current)
        layout.addWidget(self._tree, 1)

    # ---- data ----

    def _models(self):
        return list_all_models()

    def reload(self, select_id: int | None = None):
        current = select_id if select_id is not None else self._current_conv_id()
        self._tree.blockSignals(True)
        self._tree.clear()

        by_project: dict[int, list] = {}
        loose = []
        for c in db.list_conversations():
            if c.project_id:
                by_project.setdefault(c.project_id, []).append(c)
            else:
                loose.append(c)

        for proj in db.list_projects():
            node = QTreeWidgetItem(self._tree, [f"📁  {proj.name}"])
            node.setData(0, self._ROLE, ("project", proj.id))
            f = node.font(0)
            f.setBold(True)
            node.setFont(0, f)
            for c in by_project.get(proj.id, []):
                self._conv_item(node, c)
            node.setExpanded(True)

        for c in loose:
            self._conv_item(self._tree, c)

        self._tree.blockSignals(False)
        if current is not None:
            self.select(current)
        if self._current_conv_id() is None:
            first = self._first_conv_item()
            if first:
                self._tree.setCurrentItem(first)

    def _conv_item(self, parent, conv):
        it = QTreeWidgetItem(parent, [
            f"{conv.title}\n{_relative(conv.updated_at)}  ·  {len(conv.messages)} msgs"
        ])
        it.setData(0, self._ROLE, ("conv", conv.id))
        return it

    def _iter_items(self):
        stack = [self._tree.topLevelItem(i) for i in range(self._tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _first_conv_item(self):
        for it in self._iter_items():
            kind, _ = it.data(0, self._ROLE)
            if kind == "conv":
                return it
        return None

    def select(self, conv_id: int):
        for it in self._iter_items():
            if it.data(0, self._ROLE) == ("conv", conv_id):
                self._tree.setCurrentItem(it)
                return

    def _current_conv_id(self) -> int | None:
        it = self._tree.currentItem()
        if it is None:
            return None
        kind, val = it.data(0, self._ROLE)
        return val if kind == "conv" else None

    def _active_project_id(self) -> int:
        """Project of the current selection (or its parent), else -1."""
        it = self._tree.currentItem()
        while it is not None:
            kind, val = it.data(0, self._ROLE)
            if kind == "project":
                return val
            it = it.parent()
        return -1

    # ---- interaction ----

    def _on_current(self, item, _prev):
        if item is None:
            return
        kind, val = item.data(0, self._ROLE)
        if kind == "conv":
            self.selected.emit(val)

    def _on_double(self, item, _col):
        kind, val = item.data(0, self._ROLE)
        if kind == "project":
            self._edit_project(val)
        else:
            self._rename_conv(val)

    def _rename_conv(self, cid: int):
        conv = next((c for c in db.list_conversations() if c.id == cid), None)
        title, ok = QInputDialog.getText(
            self, "Rename conversation", "Title", text=conv.title if conv else ""
        )
        if ok and title.strip():
            db.update_conversation(cid, title=title.strip())
            self.reload(select_id=cid)

    def _new_project(self):
        dlg = ProjectDialog(self._models(), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            db.create_project(**dlg.values())
            self.reload()

    def _edit_project(self, pid: int):
        proj = next((p for p in db.list_projects() if p.id == pid), None)
        if proj is None:
            return
        dlg = ProjectDialog(self._models(), project=proj, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            db.update_project(pid, **dlg.values())
            self.reload()

    def _menu(self, pos):
        item = self._tree.itemAt(pos)
        if item is None:
            return
        kind, val = item.data(0, self._ROLE)
        menu = QMenu(self)
        if kind == "project":
            menu.addAction("New chat here").triggered.connect(lambda: self.new_requested.emit(val))
            menu.addAction("Edit project…").triggered.connect(lambda: self._edit_project(val))
            menu.addSeparator()
            menu.addAction("Delete project").triggered.connect(lambda: self._delete_project(val))
        else:
            menu.addAction("Rename").triggered.connect(lambda: self._rename_conv(val))
            move = menu.addMenu("Move to project")
            for p in db.list_projects():
                move.addAction(p.name).triggered.connect(
                    lambda _c=False, pid=p.id, cid=val: self._move(cid, pid)
                )
            move.addSeparator()
            move.addAction("New project…").triggered.connect(
                lambda _c=False, cid=val: self._move_to_new(cid)
            )
            move.addAction("Remove from project").triggered.connect(
                lambda _c=False, cid=val: self._move(cid, None)
            )
            menu.addSeparator()
            menu.addAction("Delete").triggered.connect(lambda: self._delete_conv(val))
        menu.exec(self._tree.viewport().mapToGlobal(pos))

    def _move(self, cid: int, pid: int | None):
        db.update_conversation(cid, project_id=pid)
        self.reload(select_id=cid)

    def _move_to_new(self, cid: int):
        dlg = ProjectDialog(self._models(), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            proj = db.create_project(**dlg.values())
            self._move(cid, proj.id)

    def _delete_project(self, pid: int):
        if QMessageBox.question(
            self, "Delete project",
            "Delete this project? Its chats are kept and moved out of the project.",
        ) == QMessageBox.StandardButton.Yes:
            db.delete_project(pid)
            self.reload()

    def _delete_conv(self, cid: int):
        if len(db.list_conversations()) <= 1:
            QMessageBox.information(self, "Keep one", "At least one conversation is kept.")
            return
        if QMessageBox.question(self, "Delete conversation", "Delete this conversation?") == \
                QMessageBox.StandardButton.Yes:
            db.delete_conversation(cid)
            self.reload()


# =====================================================================
# Chat view (single, reloadable)
# =====================================================================


class ChatView(QWidget):
    meta_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.conv_id: int | None = None
        self._models: list[LocalModel] = []
        self._reset_stream_state()

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(80)
        self._repaint_timer.timeout.connect(self._flush_stream)

        self._build_ui()

    def _reset_stream_state(self):
        self._streaming = False
        self._current_response = ""
        self._comparison_response = ""
        self._thread = None
        self._comparison_thread = None
        self._stream_tokens = 0
        self._stream_start = 0.0
        self._cmp_tokens = 0
        self._cmp_start = 0.0
        self._base_html = ""
        self._comparison_mode = False

    # ---- construction ----

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        col = QVBoxLayout(left)
        col.setContentsMargins(22, 6, 22, 18)
        col.setSpacing(10)

        # control strip
        strip_w = QWidget()
        strip_w.setObjectName("chatStrip")
        strip = QHBoxLayout(strip_w)
        strip.setContentsMargins(0, 0, 0, 8)
        strip.setSpacing(8)
        model_lbl = QLabel("Model")
        model_lbl.setObjectName("fieldLabel")
        strip.addWidget(model_lbl)
        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(230)
        self._model_combo.setToolTip("The model that answers in this conversation")
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        strip.addWidget(self._model_combo)
        strip.addStretch()
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setObjectName("toggleBtn")
        self._compare_btn.setCheckable(True)
        self._compare_btn.setToolTip("Answer with a second model side by side")
        self._compare_btn.clicked.connect(self._toggle_comparison)
        self._settings_btn = QPushButton("Settings")
        self._settings_btn.setObjectName("toggleBtn")
        self._settings_btn.setCheckable(True)
        self._settings_btn.setToolTip("System prompt, response style, metrics")
        self._settings_btn.clicked.connect(self._toggle_settings)
        strip.addWidget(self._compare_btn)
        strip.addWidget(self._settings_btn)
        col.addWidget(strip_w)

        # transcripts
        self._chat_area = QSplitter(Qt.Orientation.Horizontal)
        self._chat_area.setObjectName("chatArea")
        self._chat_display = QTextEdit(readOnly=True)
        self._chat_display.setObjectName("chatDisplay")
        self._compare_display = QTextEdit(readOnly=True)
        self._compare_display.setObjectName("chatDisplay")
        self._main_pane, self._main_title, self._main_stat = self._pane(self._chat_display)
        self._cmp_pane, self._cmp_title, self._cmp_stat = self._pane(self._compare_display)
        self._chat_area.addWidget(self._main_pane)
        self._chat_area.addWidget(self._cmp_pane)
        self._cmp_pane.hide()
        col.addWidget(self._chat_area, 1)

        self._compare_row = QWidget()
        crow = QHBoxLayout(self._compare_row)
        crow.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Compare against")
        lbl.setObjectName("fieldLabel")
        crow.addWidget(lbl)
        self._compare_combo = QComboBox()
        self._compare_combo.setMinimumWidth(230)
        self._compare_combo.currentIndexChanged.connect(self._update_pane_titles)
        crow.addWidget(self._compare_combo)
        crow.addStretch()
        self._compare_row.hide()
        col.addWidget(self._compare_row)

        # composer
        composer = QWidget()
        composer.setObjectName("composer")
        cl = QVBoxLayout(composer)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(8)
        self._input = QTextEdit()
        self._input.setObjectName("messageInput")
        self._input.setPlaceholderText("Message…  (⌘↩ to send)")
        self._input.setMaximumHeight(120)
        self._input.textChanged.connect(self._update_send_enabled)
        self._input.installEventFilter(self)
        cl.addWidget(self._input)

        brow = QHBoxLayout()
        brow.setSpacing(6)
        self._more_btn = QPushButton("⋯")
        self._more_btn.setObjectName("quiet")
        self._more_btn.setFixedWidth(30)
        self._more_btn.setToolTip("More actions")
        self._more_btn.clicked.connect(self._show_more_menu)
        self._regen_btn = QPushButton("Regenerate")
        self._regen_btn.setObjectName("quiet")
        self._regen_btn.setToolTip("Redo the last answer")
        self._regen_btn.clicked.connect(self._regenerate)
        brow.addWidget(self._more_btn)
        brow.addWidget(self._regen_btn)
        brow.addStretch()
        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("primary")
        self._send_btn.clicked.connect(self._composer_action)
        brow.addWidget(self._send_btn)
        cl.addLayout(brow)
        col.addWidget(composer)

        self._splitter.addWidget(left)
        self._side_panel = self._build_side_panel()
        self._side_panel.hide()
        self._splitter.addWidget(self._side_panel)
        self._splitter.setSizes([860, 0])
        root.addWidget(self._splitter)

    def _pane(self, display: QTextEdit):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        title = QLabel("")
        title.setObjectName("paneTitle")
        stat = QLabel("")
        stat.setObjectName("paneStat")
        for lab in (title, stat):
            lab.hide()
        v.addWidget(title)
        v.addWidget(stat)
        v.addWidget(display, 1)
        return w, title, stat

    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("chatSidebar")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        layout.addWidget(self._section("System prompt"))
        self._system_prompt = QTextEdit()
        self._system_prompt.setPlaceholderText("You are a helpful assistant…")
        self._system_prompt.setMaximumHeight(110)
        self._system_prompt.textChanged.connect(self._save_system_prompt)
        layout.addWidget(self._system_prompt)

        layout.addWidget(self._section("Response style"))
        self._preset = SegmentedControl(list(_PRESETS))
        self._preset.set_current("Balanced")
        self._preset.currentChanged.connect(self._apply_preset)
        layout.addWidget(self._preset)
        self._preset_desc = QLabel(_PRESETS["Balanced"][2])
        self._preset_desc.setObjectName("presetDesc")
        self._preset_desc.setWordWrap(True)
        layout.addWidget(self._preset_desc)

        adv = QWidget()
        af = QFormLayout(adv)
        af.setContentsMargins(0, 6, 0, 0)
        af.setSpacing(8)
        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 200)
        self._temp_label = QLabel("0.70")
        self._temp_label.setObjectName("mono")
        self._temp_slider.valueChanged.connect(lambda v: self._temp_label.setText(f"{v / 100:.2f}"))
        af.addRow("Temperature", self._slider_row(self._temp_slider, self._temp_label))
        self._topp_slider = QSlider(Qt.Orientation.Horizontal)
        self._topp_slider.setRange(0, 100)
        self._topp_label = QLabel("0.90")
        self._topp_label.setObjectName("mono")
        self._topp_slider.valueChanged.connect(lambda v: self._topp_label.setText(f"{v / 100:.2f}"))
        af.addRow("Top-p", self._slider_row(self._topp_slider, self._topp_label))
        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(64, 32768)
        self._max_tokens.setValue(2048)
        self._max_tokens.setSingleStep(128)
        af.addRow("Max tokens", self._max_tokens)
        layout.addWidget(Disclosure("Advanced", adv))
        self._apply_preset("Balanced")

        layout.addWidget(self._section("Metrics"))
        mform = QFormLayout()
        mform.setContentsMargins(0, 0, 0, 0)
        mform.setSpacing(6)
        self._tps_label = QLabel("—")
        self._elapsed_label = QLabel("—")
        self._tokens_label = QLabel("—")
        for lab in (self._tps_label, self._elapsed_label, self._tokens_label):
            lab.setObjectName("mono")
        mform.addRow("Throughput", self._tps_label)
        mform.addRow("Elapsed", self._elapsed_label)
        mform.addRow("Tokens", self._tokens_label)
        layout.addLayout(mform)
        layout.addStretch()
        return panel

    def _section(self, title: str) -> QLabel:
        lbl = QLabel(title.upper())
        lbl.setObjectName("sectionLabel")
        f = lbl.font()
        f.setLetterSpacing(f.SpacingType.AbsoluteSpacing, 0.8)
        lbl.setFont(f)
        return lbl

    def _slider_row(self, slider, value) -> QWidget:
        w = QWidget()
        r = QHBoxLayout(w)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(slider)
        r.addWidget(value)
        return w

    def _apply_preset(self, name: str):
        temp, topp, desc = _PRESETS[name]
        self._temp_slider.setValue(int(temp * 100))
        self._topp_slider.setValue(int(topp * 100))
        self._preset_desc.setText(desc)

    # ---- loading ----

    def load(self, conv_id: int):
        if self._streaming:
            self._stop()
        self._repaint_timer.stop()
        self._reset_stream_state()
        self.conv_id = conv_id
        conv = self._conv()
        self._system_prompt.blockSignals(True)
        self._system_prompt.setPlainText(conv.system_prompt if conv else "")
        self._system_prompt.blockSignals(False)
        self._compare_btn.setChecked(False)
        self._toggle_comparison(False)
        self._refresh_model_list(conv.model_name if conv else "", conv.backend if conv else "")
        self._render_all(conv)
        self._update_send_enabled()

    def _conv(self):
        return next((c for c in db.list_conversations() if c.id == self.conv_id), None)

    def _refresh_model_list(self, selected_name: str = "", selected_backend: str = ""):
        self._models = list_all_models()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._compare_combo.clear()
        for m in self._models:
            label = f"{m.name}  ·  {m.display_backend}"
            self._model_combo.addItem(label)
            self._compare_combo.addItem(label)
        self._model_combo.blockSignals(False)
        if selected_name:
            for i, m in enumerate(self._models):
                if m.name == selected_name and m.backend == selected_backend:
                    self._model_combo.setCurrentIndex(i)
                    break
        if self._compare_combo.count() > 1 and self._compare_combo.currentIndex() == self._model_combo.currentIndex():
            self._compare_combo.setCurrentIndex((self._model_combo.currentIndex() + 1) % self._compare_combo.count())
        self._update_send_enabled()
        self._emit_meta()

    def _current_model(self) -> LocalModel | None:
        i = self._model_combo.currentIndex()
        return self._models[i] if 0 <= i < len(self._models) else None

    def _compare_model(self) -> LocalModel | None:
        i = self._compare_combo.currentIndex()
        return self._models[i] if 0 <= i < len(self._models) else None

    def _on_model_changed(self):
        model = self._current_model()
        if model and self.conv_id is not None:
            db.update_conversation(self.conv_id, model_name=model.name, backend=model.backend)
        self._update_pane_titles()
        self._update_send_enabled()
        self._emit_meta()

    def _save_system_prompt(self):
        if self.conv_id is not None:
            db.update_conversation(self.conv_id, system_prompt=self._system_prompt.toPlainText())

    def _emit_meta(self, live: str = ""):
        self.meta_changed.emit(live.strip())

    def _get_params(self) -> dict:
        return {
            "temperature": self._temp_slider.value() / 100,
            "top_p": self._topp_slider.value() / 100,
            "max_tokens": self._max_tokens.value(),
        }

    def _update_send_enabled(self):
        if self._streaming:
            self._send_btn.setEnabled(True)
            return
        ready = bool(self._input.toPlainText().strip()) and self._current_model() is not None
        self._send_btn.setEnabled(ready)
        self._send_btn.setToolTip(
            "" if ready else "Pick a model and type a message first"
        )
        conv = self._conv()
        self._regen_btn.setEnabled(bool(conv and conv.messages))

    # ---- rendering ----

    def _messages_html(self, messages) -> str:
        return "".join(_bubble(m.role, _render_markdown(m.content)) for m in messages)

    def _render_all(self, conv=None):
        conv = conv or self._conv()
        if conv is None:
            return
        if not conv.messages:
            self._chat_display.setHtml(_empty_doc(self._current_model() is not None))
            return
        doc = _chat_css() + "<body>" + self._messages_html(conv.messages) + "</body>"
        self._chat_display.setHtml(doc)
        self._scroll_to_bottom(self._chat_display)

    def _scroll_to_bottom(self, display: QTextEdit):
        sb = display.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _flush_stream(self):
        if not self._streaming:
            return
        if self._current_response:
            body = self._base_html + _bubble("assistant", _render_markdown(self._current_response), cursor=True)
            self._chat_display.setHtml(_chat_css() + "<body>" + body + "</body>")
            self._scroll_to_bottom(self._chat_display)
        if self._comparison_mode and self._comparison_response:
            body = self._base_html + _bubble("assistant", _render_markdown(self._comparison_response), cursor=True)
            self._compare_display.setHtml(_chat_css() + "<body>" + body + "</body>")
            self._scroll_to_bottom(self._compare_display)

    # ---- send / stream ----

    def _build_messages(self) -> list[dict]:
        conv = self._conv()
        if conv is None:
            return []
        msgs: list[dict] = []
        if conv.system_prompt.strip():
            msgs.append({"role": "system", "content": conv.system_prompt.strip()})
        for m in conv.messages:
            msgs.append({"role": m.role, "content": m.content})
        return msgs

    def _send(self):
        text = self._input.toPlainText().strip()
        model = self._current_model()
        if not text or self._streaming or model is None:
            return
        db.add_message(self.conv_id, "user", text)
        self._input.clear()
        self._render_all()
        self._start_inference(model)

    def _start_inference(self, model: LocalModel):
        from llm_manager.gui.workers.inference_worker import InferenceThread

        conv = self._conv()
        self._base_html = self._messages_html(conv.messages) if conv else ""
        self._streaming = True
        self._current_response = ""
        self._comparison_response = ""
        self._stream_start = time.time()
        self._stream_tokens = 0
        self._cmp_start = time.time()
        self._cmp_tokens = 0
        self._send_btn.setText("Stop")
        self._update_send_enabled()

        messages = self._build_messages()
        params = self._get_params()

        self._thread = InferenceThread(get_backend(model.backend), model, messages, params)
        self._thread.token_received.connect(lambda t: self._on_token(t, "main"))
        self._thread.finished.connect(self._on_inference_done)
        self._thread.error_occurred.connect(self._on_error)
        self._thread.start()

        if self._comparison_mode:
            cmp_model = self._compare_model()
            if cmp_model:
                self._comparison_thread = InferenceThread(
                    get_backend(cmp_model.backend), cmp_model, messages, params
                )
                self._comparison_thread.token_received.connect(lambda t: self._on_token(t, "cmp"))
                self._comparison_thread.finished.connect(self._on_cmp_done)
                self._comparison_thread.error_occurred.connect(self._on_error)
                self._comparison_thread.start()

    def _on_token(self, token: str, which: str):
        if which == "main":
            self._current_response += token
            self._stream_tokens += 1
            elapsed = time.time() - self._stream_start
            if elapsed > 0:
                tps = self._stream_tokens / elapsed
                self._tps_label.setText(f"{tps:.1f} tok/s")
                self._elapsed_label.setText(f"{elapsed:.1f} s")
                self._tokens_label.setText(str(self._stream_tokens))
                self._main_stat.setText(f"{tps:.0f} tok/s · {self._stream_tokens} tokens")
                self._emit_meta(f"{tps:.0f} tok/s")
        else:
            self._comparison_response += token
            self._cmp_tokens += 1
            el = time.time() - self._cmp_start
            if el > 0:
                self._cmp_stat.setText(f"{self._cmp_tokens / el:.0f} tok/s · {self._cmp_tokens} tokens")
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _on_inference_done(self, tps: float, elapsed: float, count: int):
        self._repaint_timer.stop()
        self._streaming = False
        db.add_message(
            self.conv_id, "assistant", self._current_response, tokens_per_sec=tps, elapsed=elapsed
        )
        self._send_btn.setText("Send")
        self._render_all()
        self._tps_label.setText(f"{tps:.1f} tok/s")
        self._elapsed_label.setText(f"{elapsed:.1f} s")
        self._tokens_label.setText(str(count))
        self._main_stat.setText(f"{tps:.0f} tok/s · {elapsed:.1f}s · {count} tokens")
        self._update_send_enabled()
        self._emit_meta()

    def _on_cmp_done(self, tps: float, elapsed: float, count: int):
        self._cmp_stat.setText(f"{tps:.0f} tok/s · {elapsed:.1f}s · {count} tokens")
        self._flush_stream()

    def _on_error(self, msg: str):
        self._repaint_timer.stop()
        self._streaming = False
        self._send_btn.setText("Send")
        self._update_send_enabled()
        QMessageBox.critical(self, "Inference error", msg)

    def _composer_action(self):
        self._stop() if self._streaming else self._send()

    def _stop(self):
        if self._thread:
            self._thread.stop()
        if self._comparison_thread:
            self._comparison_thread.stop()

    def _show_more_menu(self):
        menu = QMenu(self)
        menu.addAction("Clear messages").triggered.connect(self._clear_chat)
        menu.exec(self._more_btn.mapToGlobal(self._more_btn.rect().bottomLeft()))

    def _toggle_settings(self, checked: bool):
        self._side_panel.setVisible(checked)
        total = max(self._splitter.width(), 800)
        self._splitter.setSizes([total - 310, 310] if checked else [total, 0])

    def _regenerate(self):
        if self._streaming:
            return
        conv = self._conv()
        if not (conv and conv.messages):
            return
        if conv.messages[-1].role == "assistant":
            db.delete_last_messages(self.conv_id, n=1)
        self._render_all()
        model = self._current_model()
        if model:
            self._start_inference(model)

    def _clear_chat(self):
        if QMessageBox.question(self, "Clear messages", "Delete every message in this conversation?") == \
                QMessageBox.StandardButton.Yes:
            db.delete_last_messages(self.conv_id, n=10_000)
            self._render_all()
            self._update_send_enabled()

    def _toggle_comparison(self, checked: bool):
        self._comparison_mode = checked
        self._compare_row.setVisible(checked)
        self._cmp_pane.setVisible(checked)
        for lab in (self._main_title, self._main_stat, self._cmp_title, self._cmp_stat):
            lab.setVisible(checked)
        if checked:
            self._chat_area.setSizes([1, 1])
        self._update_pane_titles()

    def _update_pane_titles(self, *_):
        if not self._comparison_mode:
            return
        main = self._current_model()
        cmp = self._compare_model()
        self._main_title.setText(main.name if main else "—")
        self._cmp_title.setText(cmp.name if cmp else "Pick a model to compare")

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            ke = QKeyEvent(event)
            if ke.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    ke.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                self._send()
                return True
        return super().eventFilter(obj, event)

    # ---- public ----

    def refresh_models(self):
        conv = self._conv()
        self._refresh_model_list(conv.model_name if conv else "", conv.backend if conv else "")

    def retheme(self):
        self._render_all()

    def focus_input(self):
        self._input.setFocus()


# =====================================================================
# Chat page (header + rail + view)
# =====================================================================


class ChatPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self._load_or_create_initial()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = PageHeader("Chat")
        layout.addWidget(self._header)

        self._split = QSplitter(Qt.Orientation.Horizontal)

        # rail lives in a stack: page 0 = full rail, page 1 = a slim expander strip
        self._rail_stack = QStackedWidget()
        self._rail = ConversationRail()
        self._rail._collapse_btn.clicked.connect(lambda: self._set_rail(False))
        strip = QWidget()
        strip.setObjectName("railStrip")
        sl = QVBoxLayout(strip)
        sl.setContentsMargins(4, 12, 4, 12)
        expand = QToolButton()
        expand.setText("›")
        expand.setObjectName("railCollapse")
        expand.setToolTip("Show the conversation list")
        expand.clicked.connect(lambda: self._set_rail(True))
        sl.addWidget(expand)
        sl.addStretch()
        self._rail_stack.addWidget(self._rail)
        self._rail_stack.addWidget(strip)

        self._view = ChatView()
        self._view.meta_changed.connect(self._header.set_meta)
        self._split.addWidget(self._rail_stack)
        self._split.addWidget(self._view)
        self._split.setStretchFactor(1, 1)
        self._split.setSizes([252, 900])
        layout.addWidget(self._split, 1)

        self._rail.selected.connect(self._view.load)
        self._rail.new_requested.connect(self.new_conversation)

    def _set_rail(self, open_: bool):
        self._rail_stack.setCurrentIndex(0 if open_ else 1)
        total = max(self._split.width(), 900)
        self._split.setSizes([252, total - 252] if open_ else [30, total - 30])

    def _load_or_create_initial(self):
        convs = db.list_conversations()
        if convs:
            self._rail.reload(select_id=convs[0].id)
        else:
            self.new_conversation()

    def new_conversation(self, project_id: int = -1):
        proj = None
        if project_id and project_id > 0:
            proj = next((p for p in db.list_projects() if p.id == project_id), None)
        conv = db.create_conversation(
            project_id=proj.id if proj else None,
            system_prompt=proj.instructions if proj else "",
            model_name=proj.model_name if proj else "",
            backend=proj.backend if proj else "",
        )
        self._rail.reload(select_id=conv.id)

    # ---- API used by MainWindow ----

    def current_view(self) -> ChatView:
        return self._view

    def refresh_models(self):
        self._view.refresh_models()

    def select_model(self, model_name: str, backend: str):
        self._view._refresh_model_list(model_name, backend)

    def focus_input(self):
        self._view.focus_input()

    def retheme(self):
        self._view.retheme()
