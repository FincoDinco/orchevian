from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from llm_manager import database as db
from llm_manager.backends import get_backend, list_all_models
from llm_manager.format import format_bytes
from llm_manager.gui.widgets import PageHeader, SegmentedControl, reveal_in_finder
from llm_manager.models import LocalModel

_FILTERS = ["All", "MLX", "Ollama", "GGUF", "Favorites"]


class ModelDetailsDialog(QDialog):
    def __init__(self, model: LocalModel, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Model details — {model.name}")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.addRow("Name", QLabel(model.name))
        form.addRow("Backend", QLabel(model.display_backend))
        form.addRow("Size", QLabel(format_bytes(model.size)))
        if model.path:
            path_lbl = QLabel(str(model.path))
            path_lbl.setWordWrap(True)
            form.addRow("Path", path_lbl)
        if model.modified:
            form.addRow("Modified", QLabel(model.modified.strftime("%Y-%m-%d %H:%M")))
        for key, val in model.details.items():
            if val:
                form.addRow(key.replace("_", " ").title(), QLabel(str(val)))
        layout.addLayout(form)

        buttons = QHBoxLayout()
        if model.path:
            reveal = QPushButton("Reveal in Finder")
            reveal.clicked.connect(lambda: reveal_in_finder(model.path))
            buttons.addWidget(reveal)
        buttons.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)


class _FetchThread(QThread):
    """Run a no-arg callable off the UI thread and hand back its result (or None)."""

    done = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception:  # noqa: BLE001 — surfaced as "unavailable" in the UI
            self.done.emit(None)


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


class HFSearchDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search Hugging Face")
        self.setMinimumSize(720, 560)
        self._results: list[dict] = []
        self._dl_thread = None
        self._card_thread = None
        self._files_thread = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search models — e.g. 'mistral 7b instruct'")
        self._search_input.returnPressed.connect(self._search)
        search_row.addWidget(self._search_input)
        search_btn = QPushButton("Search")
        search_btn.setObjectName("primary")
        search_btn.clicked.connect(self._search)
        search_row.addWidget(search_btn)
        layout.addLayout(search_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Model", "Task", "Downloads", "Likes"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 340)
        self._table.setColumnWidth(1, 130)
        self._table.setColumnWidth(2, 100)
        self._table.itemSelectionChanged.connect(self._on_select)
        layout.addWidget(self._table, 1)

        self._blurb = QLabel("Select a model to see details.")
        self._blurb.setObjectName("detailLabel")
        self._blurb.setWordWrap(True)
        self._blurb.setMinimumHeight(52)
        self._blurb.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._blurb)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self._backend_combo = QComboBox()
        self._backend_combo.addItems(["mlx", "ollama", "gguf"])
        self._backend_combo.currentTextChanged.connect(self._on_backend_changed)
        form.addRow("Format", self._backend_combo)
        self._file_combo = QComboBox()
        self._file_combo.setEditable(True)
        self._file_combo.setEnabled(False)
        self._file_combo.lineEdit().setPlaceholderText("choose a .gguf quantisation")
        form.addRow("File", self._file_combo)
        layout.addLayout(form)

        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)
        self._status = QLabel("")
        self._status.setObjectName("detailLabel")
        layout.addWidget(self._status)

        btns = QHBoxLayout()
        btns.addStretch()
        self._dl_btn = QPushButton("Add to library")
        self._dl_btn.setObjectName("primary")
        self._dl_btn.setEnabled(False)
        self._dl_btn.clicked.connect(self._download)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(self._dl_btn)
        btns.addWidget(cancel)
        layout.addLayout(btns)

    # ---- search ----

    def _search(self):
        query = self._search_input.text().strip()
        if not query:
            return
        self._status.setText("Searching…")
        from llm_manager import hub

        try:
            self._results = hub.search_models(query, limit=25)
        except Exception as e:  # noqa: BLE001
            self._status.setText(f"Search failed: {e}")
            return

        self._table.setRowCount(0)
        for r in self._results:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(r["id"]))
            self._table.setItem(row, 1, QTableWidgetItem(r["pipeline_tag"]))
            self._table.setItem(row, 2, QTableWidgetItem(_fmt_count(r["downloads"])))
            self._table.setItem(row, 3, QTableWidgetItem(_fmt_count(r["likes"])))
        self._status.setText(f"{len(self._results)} results — most downloaded first")

    # ---- selection ----

    def _selected(self) -> dict | None:
        row = self._table.currentRow()
        return self._results[row] if 0 <= row < len(self._results) else None

    def _on_select(self):
        r = self._selected()
        if r is None:
            return
        self._dl_btn.setEnabled(True)
        rid = r["id"]
        tags = ", ".join(t for t in r["tags"][:6] if "/" not in t)
        self._blurb.setText(
            f"⬇ {_fmt_count(r['downloads'])}  ·  ♥ {_fmt_count(r['likes'])}"
            f"{'  ·  ' + tags if tags else ''}\nLoading description…"
        )
        self._backend_combo.setCurrentText("gguf" if "gguf" in rid.lower() else "mlx")

        from llm_manager import hub

        stats_line = self._blurb.text().split("\n")[0]
        self._card_thread = _FetchThread(lambda: hub.get_model_card(rid), self)
        self._card_thread.done.connect(
            lambda text: self._blurb.setText(stats_line + "\n" + _first_line(text))
        )
        self._card_thread.start()
        self._maybe_load_files()

    def _on_backend_changed(self, backend: str):
        is_gguf = backend == "gguf"
        self._file_combo.setEnabled(is_gguf)
        if is_gguf:
            self._maybe_load_files()

    def _maybe_load_files(self):
        r = self._selected()
        if r is None or self._backend_combo.currentText() != "gguf":
            return
        self._file_combo.clear()
        self._file_combo.lineEdit().setPlaceholderText("loading files…")
        from llm_manager import hub

        rid = r["id"]
        self._files_thread = _FetchThread(lambda: hub.list_gguf_files(rid), self)
        self._files_thread.done.connect(self._fill_files)
        self._files_thread.start()

    def _fill_files(self, files):
        self._file_combo.clear()
        if files:
            self._file_combo.addItems(files)
            self._file_combo.lineEdit().setPlaceholderText("")
        else:
            self._file_combo.lineEdit().setPlaceholderText("no .gguf files found — type one")

    # ---- download ----

    def _download(self):
        from llm_manager.gui.workers.download_worker import DownloadThread

        r = self._selected()
        if r is None:
            return
        repo_id = r["id"]
        backend = self._backend_combo.currentText()
        filename = self._file_combo.currentText().strip() if backend == "gguf" else ""
        if backend == "gguf" and not filename:
            QMessageBox.warning(self, "Pick a file", "Choose a .gguf file to download.")
            return

        self._dl_btn.setEnabled(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.show()
        self._status.setText(f"Downloading {repo_id}…")
        self._dl_thread = DownloadThread(backend, repo_id, filename)
        self._dl_thread.progress.connect(self._on_progress)
        self._dl_thread.finished.connect(self._on_done)
        self._dl_thread.error_occurred.connect(self._on_error)
        self._dl_thread.start()

    def _on_progress(self, pct: int, msg: str):
        if pct < 0:
            self._progress.setRange(0, 0)  # indeterminate (e.g. Ollama pull)
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(pct)
        self._status.setText(msg)

    def _on_done(self, path: str):
        self._progress.hide()
        self._status.setText(f"Added — {path}")
        self.accept()

    def _on_error(self, msg: str):
        self._progress.hide()
        self._dl_btn.setEnabled(True)
        self._status.setText(f"Error: {msg}")


def _first_line(text: str, limit: int = 220) -> str:
    if not text:
        return "No description available."
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not s.startswith("---") and not s.startswith("license"):
            return s[:limit] + ("…" if len(s) > limit else "")
    return "No description available."


class ModelsPage(QWidget):
    chat_with_model = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._models: list[LocalModel] = []
        self._rows: list[LocalModel] = []
        self._favorites: set[tuple[str, str]] = set()
        self._filter = "All"
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hf_btn = QPushButton("Search Hugging Face…")
        hf_btn.clicked.connect(self._search_hf)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self._header = PageHeader("Models", actions=[hf_btn, refresh_btn])
        layout.addWidget(self._header)

        body = QVBoxLayout()
        body.setContentsMargins(28, 18, 28, 24)
        body.setSpacing(12)

        filter_row = QHBoxLayout()
        self._segments = SegmentedControl(_FILTERS)
        self._segments.currentChanged.connect(self._set_filter)
        filter_row.addWidget(self._segments)
        filter_row.addStretch()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by name")
        self._search.setFixedWidth(220)
        self._search.textChanged.connect(self._populate_table)
        filter_row.addWidget(self._search)
        body.addLayout(filter_row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["★", "Name", "Backend", "Size", "Modified"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setColumnWidth(0, 34)
        self._table.setColumnWidth(1, 300)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 100)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.doubleClicked.connect(lambda *_: self._show_info())
        self._table.itemSelectionChanged.connect(self._update_actions)
        body.addWidget(self._table, 1)

        action_row = QHBoxLayout()
        self._chat_btn = QPushButton("Chat")
        self._chat_btn.clicked.connect(self._chat_selected)
        self._info_btn = QPushButton("Info")
        self._info_btn.clicked.connect(self._show_info)
        self._reveal_btn = QPushButton("Reveal in Finder")
        self._reveal_btn.clicked.connect(self._reveal_selected)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.clicked.connect(self._delete_selected)
        action_row.addWidget(self._chat_btn)
        action_row.addWidget(self._info_btn)
        action_row.addWidget(self._reveal_btn)
        action_row.addStretch()
        action_row.addWidget(self._delete_btn)
        body.addLayout(action_row)

        layout.addLayout(body)
        self._update_actions()

    # ---- data ----

    def refresh(self):
        self._models = list_all_models()
        self._favorites = db.list_favorites()
        self._populate_table()
        total = sum(m.size for m in self._models)
        self._header.set_meta(f"{len(self._models)} models · {format_bytes(total)}")

    def _set_filter(self, key: str):
        self._filter = key
        self._populate_table()

    def _filtered_models(self) -> list[LocalModel]:
        models = self._models
        if self._filter == "Favorites":
            models = [m for m in models if (m.name, m.backend) in self._favorites]
        elif self._filter != "All":
            models = [m for m in models if m.backend == self._filter.lower()]
        needle = self._search.text().strip().lower()
        if needle:
            models = [m for m in models if needle in m.name.lower()]
        return models

    def _populate_table(self):
        models = self._filtered_models()
        self._rows = models
        self._table.setRowCount(0)
        for m in models:
            row = self._table.rowCount()
            self._table.insertRow(row)
            is_fav = (m.name, m.backend) in self._favorites
            star = QTableWidgetItem("★" if is_fav else "☆")
            star.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, star)
            self._table.setItem(row, 1, QTableWidgetItem(m.name))
            self._table.setItem(row, 2, QTableWidgetItem(m.display_backend))
            self._table.setItem(row, 3, QTableWidgetItem(format_bytes(m.size)))
            mod = m.modified.strftime("%Y-%m-%d %H:%M") if m.modified else "—"
            self._table.setItem(row, 4, QTableWidgetItem(mod))
        self._update_actions()

    # ---- selection / actions ----

    def _selected_model(self) -> LocalModel | None:
        row = self._table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _update_actions(self):
        model = self._selected_model()
        self._chat_btn.setEnabled(model is not None)
        self._info_btn.setEnabled(model is not None)
        self._delete_btn.setEnabled(model is not None)
        self._reveal_btn.setEnabled(model is not None and model.path is not None)

    def _on_cell_clicked(self, row: int, col: int):
        if col == 0 and 0 <= row < len(self._rows):
            self._toggle_fav(self._rows[row])

    def _context_menu(self, pos):
        model = self._selected_model()
        if model is None:
            return
        menu = QMenu(self)
        act_chat = QAction("Chat", menu)
        act_chat.triggered.connect(self._chat_selected)
        act_info = QAction("Info", menu)
        act_info.triggered.connect(self._show_info)
        act_reveal = QAction("Reveal in Finder", menu)
        act_reveal.setEnabled(model.path is not None)
        act_reveal.triggered.connect(self._reveal_selected)
        act_del = QAction("Delete", menu)
        act_del.triggered.connect(self._delete_selected)
        menu.addActions([act_chat, act_info, act_reveal])
        menu.addSeparator()
        menu.addAction(act_del)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _chat_selected(self):
        model = self._selected_model()
        if model:
            self.chat_with_model.emit(model.name, model.backend)

    def _show_info(self):
        model = self._selected_model()
        if model:
            ModelDetailsDialog(model, self).exec()

    def _reveal_selected(self):
        model = self._selected_model()
        if model and model.path:
            reveal_in_finder(model.path)

    def _toggle_fav(self, model: LocalModel):
        db.toggle_favorite(model.name, model.backend)
        self._favorites = db.list_favorites()
        self._populate_table()

    def _delete_selected(self):
        model = self._selected_model()
        if model is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete model",
            f"Delete '{model.name}' ({model.display_backend})?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            get_backend(model.backend).delete(model)
            self.refresh()

    def _search_hf(self):
        dlg = HFSearchDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def focus_search(self):
        self._search.setFocus()
        self._search.selectAll()
