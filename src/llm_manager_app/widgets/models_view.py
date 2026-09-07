"""Models catalog: grouped list, availability banners, load/unload on a worker."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QModelIndex, QObject, QRect, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import LoadOptions, LocalModel, ModelRef
from llm_engine.services.session import SessionStatus
from llm_manager_app.icons import icon
from llm_manager_app.tokens import current_palette, qcolor

_BACKEND_ORDER = ("ollama", "mlx", "gguf")
_BACKEND_LABELS = {
    "ollama": "Ollama",
    "mlx": "MLX",
    "gguf": "GGUF",
}
_ID_ROLE = Qt.ItemDataRole.UserRole
_META_ROLE = Qt.ItemDataRole.UserRole + 1
_UNITS = ("B", "KB", "MB", "GB", "TB")
_SHUTDOWN_WAIT_MS = 6000
_ROW_H = 64
_HEADER_H = 36


class Catalog(Protocol):
    def list_models(self) -> tuple[list[LocalModel], dict[str, tuple[bool, str | None]]]: ...
    def load(self, ref: ModelRef, options: LoadOptions | None = None) -> LocalModel: ...
    def unload(self) -> None: ...
    def status(self) -> SessionStatus: ...


def _backend_label(name: str) -> str:
    return _BACKEND_LABELS.get(name, name.upper())


def _ordered_backend_names(
    availability: dict[str, tuple[bool, str | None]],
    models: list[LocalModel],
) -> list[str]:
    extras = set(availability) | {str(model.ref.backend) for model in models}
    ordered: list[str] = []
    for name in _BACKEND_ORDER:
        if name in extras:
            ordered.append(name)
            extras.discard(name)
    ordered.extend(sorted(extras))
    return ordered


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    unit = _UNITS[0]
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            break
        value /= 1024
    if unit == "B":
        return f"{int(value)} B"
    return f"{value:.1f} {unit}"


class ModelsDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        del option
        if index.data(_ID_ROLE):
            return QSize(160, _ROW_H)
        return QSize(160, _HEADER_H)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = current_palette()
        model_id = index.data(_ID_ROLE)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(4, 1, -4, -1)
        if model_id and (selected or hovered):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(palette.selection))
            painter.drawRoundedRect(rect, palette.radius_control, palette.radius_control)
        title = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        font = QFont(option.font)
        if not model_id:
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor(palette.secondary))
            text_rect = rect.adjusted(10, 0, -10, 0)
            painter.drawText(
                text_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                title,
            )
            painter.restore()
            return
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(palette.text))
        title_rect = QRect(rect.x() + 14, rect.y() + 10, rect.width() - 28, 22)
        elided = painter.fontMetrics().elidedText(
            title, Qt.TextElideMode.ElideRight, title_rect.width()
        )
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
            elided,
        )
        meta = str(index.data(_META_ROLE) or "")
        if meta:
            meta_font = QFont(option.font)
            if meta_font.pointSize() > 0:
                meta_font.setPointSize(max(11, meta_font.pointSize() - 1))
            painter.setFont(meta_font)
            painter.setPen(QColor(palette.secondary))
            meta_rect = QRect(rect.x() + 14, rect.y() + 34, rect.width() - 28, 20)
            painter.drawText(
                meta_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                meta,
            )
        painter.restore()


def _reveal_label() -> str:
    if sys.platform == "darwin":
        return "Reveal in Finder"
    if sys.platform == "win32":
        return "Reveal in Explorer"
    return "Reveal in file manager"


class _WorkThread(QThread):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._work: Callable[[], None] | None = None

    def run_work(self, work: Callable[[], None]) -> None:
        self._work = work
        self.start()

    def run(self) -> None:
        work = self._work
        self._work = None
        if work is not None:
            work()


def reveal_in_file_manager(path: Path) -> None:
    resolved = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", resolved])
        return
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", resolved])
        return
    folder = path if path.is_dir() else path.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class ModelsView(QWidget):
    chat_requested = Signal(object)
    refreshed = Signal()
    job_finished = Signal()
    refresh_requested = Signal()
    load_requested = Signal(object)
    unload_requested = Signal()
    stop_requested = Signal()

    _listed = Signal(object, object)
    _loaded = Signal(object)
    _unloaded = Signal()
    _failed = Signal(object)
    _job_done = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        catalog: Catalog,
        reveal: Callable[[Path], None] | None = None,
        external_jobs: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modelsView")
        self._catalog = catalog
        self._reveal = reveal if reveal is not None else reveal_in_file_manager
        self._external_jobs = external_jobs
        self._models_by_id: dict[str, LocalModel] = {}
        self._busy = False
        self._chat_busy = False
        self._closing = False
        self._job_kind: str | None = None
        self._job_lock = threading.Lock()
        self._thread = _WorkThread(self)

        self._banner = QLabel(self)
        self._banner.setObjectName("modelsBanner")
        self._banner.setWordWrap(True)
        self._banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._banner.hide()

        self._list = QListWidget(self)
        self._list.setObjectName("modelsList")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._list.setUniformItemSizes(False)
        self._list.setItemDelegate(ModelsDelegate(self._list))
        self._list.setMouseTracking(True)
        self._list.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.currentItemChanged.connect(self._on_current_item)

        self._search = QLineEdit(self)
        self._search.setObjectName("modelSearch")
        self._search.setPlaceholderText("Search models")
        self._search.setClearButtonEnabled(True)
        self._search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self._search.textChanged.connect(self._filter_models)
        self._catalog_pane = QWidget(self)
        catalog_layout = QVBoxLayout(self._catalog_pane)
        catalog_layout.setContentsMargins(0, 0, 16, 0)
        catalog_layout.setSpacing(16)
        catalog_layout.addWidget(self._search)
        catalog_layout.addWidget(self._list, 1)
        self._list_empty = QLabel("No models yet. Refresh to check your backends.", self)
        self._list_empty.setObjectName("catalogEmpty")
        self._list_empty.setWordWrap(True)
        catalog_layout.addWidget(self._list_empty)

        heading = QVBoxLayout()
        title = QLabel("Model library", self)
        title.setObjectName("pageTitle")
        self._summary = QLabel("Your local models, in one place.", self)
        self._summary.setObjectName("pageSubtitle")
        heading.addWidget(title)
        heading.addWidget(self._summary)
        self._refresh_btn = QPushButton("Refresh", self)
        self._refresh_btn.setIcon(icon("refresh"))
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self.refresh)
        header = QHBoxLayout()
        header.addLayout(heading, 1)
        header.addWidget(self._refresh_btn)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(28, 24, 28, 24)
        self._layout.setSpacing(24)
        self._layout.addLayout(header)
        self._layout.addWidget(self._banner)
        self._layout.addWidget(self._catalog_pane, 1)

        self._detail = QWidget(self)
        self._detail.setObjectName("detailPane")
        self._detail.hide()
        self._error = QLabel(self._detail)
        self._error.setObjectName("modelsError")
        self._error.setWordWrap(True)
        self._error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._error.hide()
        self._body = QLabel(self._detail)
        self._body.setObjectName("modelsDetailBody")
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._body.setTextFormat(Qt.TextFormat.PlainText)
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._body.setText("Select a model")
        self._model_name = QLabel("Select a model", self._detail)
        self._model_name.setObjectName("modelTitle")
        self._model_name.setTextFormat(Qt.TextFormat.PlainText)
        self._model_name.setWordWrap(True)
        self._model_state = QLabel("Inspect a model to see its details and start a conversation.")
        self._model_state.setObjectName("pageSubtitle")
        self._model_state.setWordWrap(True)
        self._facts = QWidget(self._detail)
        self._facts.setObjectName("modelFacts")
        self._facts.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        facts_layout = QHBoxLayout(self._facts)
        facts_layout.setContentsMargins(18, 18, 18, 18)
        self._fact_values: dict[str, QLabel] = {}
        for label in ("BACKEND", "DISK SIZE"):
            column = QVBoxLayout()
            caption = QLabel(label, self._facts)
            caption.setObjectName("eyebrow")
            value = QLabel("—", self._facts)
            value.setObjectName("factValue")
            column.addWidget(caption)
            column.addWidget(value)
            facts_layout.addLayout(column, 1)
            self._fact_values[label] = value

        self._load_btn = QPushButton("Load", self._detail)
        self._load_btn.setObjectName("loadButton")
        self._load_btn.clicked.connect(self.load_selected)
        self._unload_btn = QPushButton("Unload", self._detail)
        self._unload_btn.setObjectName("unloadButton")
        self._unload_btn.clicked.connect(self.unload_loaded)
        self._stop_btn = QPushButton("Cancel loading", self._detail)
        self._stop_btn.setObjectName("cancelModelLoadButton")
        self._stop_btn.clicked.connect(self._cancel_load)
        self._stop_btn.hide()
        self._reveal_btn = QPushButton(_reveal_label(), self._detail)
        self._reveal_btn.setObjectName("revealButton")
        self._reveal_btn.clicked.connect(self.reveal_selected)
        self._chat_btn = QPushButton("Chat with this model", self._detail)
        self._chat_btn.setObjectName("chatButton")
        self._chat_btn.clicked.connect(self.chat_with_selected)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self._load_btn)
        actions.addWidget(self._unload_btn)
        actions.addWidget(self._stop_btn)
        actions.addStretch(1)

        detail_layout = QVBoxLayout(self._detail)
        detail_layout.setContentsMargins(24, 8, 4, 8)
        detail_layout.setSpacing(20)
        detail_layout.addWidget(self._error)
        detail_layout.addWidget(self._model_name)
        detail_layout.addWidget(self._model_state)
        detail_layout.addWidget(self._chat_btn, 0, Qt.AlignmentFlag.AlignLeft)
        detail_layout.addWidget(self._facts)
        details_label = QLabel("MODEL DETAILS", self._detail)
        details_label.setObjectName("eyebrow")
        detail_layout.addWidget(details_label)
        detail_layout.addWidget(self._body)
        detail_layout.addLayout(actions)
        detail_layout.addWidget(self._reveal_btn, 0, Qt.AlignmentFlag.AlignLeft)
        detail_layout.addStretch(1)
        for button in (self._load_btn, self._unload_btn, self._chat_btn, self._reveal_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)

        queued = Qt.ConnectionType.QueuedConnection
        self._listed.connect(self._on_listed, queued)
        self._loaded.connect(self._on_loaded, queued)
        self._unloaded.connect(self._on_unloaded, queued)
        self._failed.connect(self._on_failed, queued)
        self._job_done.connect(self._on_job_done, queued)
        self._sync_actions()

    @property
    def detail(self) -> QWidget:
        return self._detail

    def embed_detail(self) -> None:
        """Combine catalog and details into one full workspace."""
        self._layout.removeWidget(self._catalog_pane)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        split.setObjectName("modelWorkspaceSplitter")
        split.setHandleWidth(1)
        split.setChildrenCollapsible(False)
        split.addWidget(self._catalog_pane)
        self._catalog_pane.setMinimumWidth(220)
        scroll = QScrollArea(split)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._detail)
        self._detail.show()
        split.addWidget(scroll)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setSizes([300, 560])
        self._layout.addWidget(split, 1)

    def _filter_models(self) -> None:
        query = self._search.text().strip().casefold()
        header: QListWidgetItem | None = None
        matches = 0
        visible = 0
        for row in range(self._list.count()):
            item = self._list.item(row)
            if not item.data(_ID_ROLE):
                if header is not None:
                    header.setHidden(bool(query) and matches == 0)
                header, matches = item, 0
                continue
            match = query in f"{item.text()} {item.data(_ID_ROLE)}".casefold()
            item.setHidden(not match)
            matches += int(match)
            visible += int(match)
        if header is not None:
            header.setHidden(bool(query) and matches == 0)
        self._list_empty.setVisible(visible == 0)
        self._list_empty.setText(
            "No models match your search."
            if query
            else "No models found. Check your backend connection, then refresh."
        )
        current = self._list.currentItem()
        if current is not None and current.isHidden():
            self._list.setCurrentRow(-1)
        if self._list.currentItem() is None:
            for row in range(self._list.count()):
                item = self._list.item(row)
                if item.data(_ID_ROLE) and not item.isHidden():
                    self._list.setCurrentItem(item)
                    break

    def selected_model(self) -> LocalModel | None:
        item = self._list.currentItem()
        if item is None:
            return None
        model_id = item.data(_ID_ROLE)
        if not model_id:
            return None
        return self._models_by_id.get(str(model_id))

    def select_id(self, model_id: str | None) -> None:
        if not model_id:
            self._list.setCurrentRow(-1)
            return
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is not None and item.data(_ID_ROLE) == model_id:
                self._list.setCurrentItem(item)
                return
        self._list.setCurrentRow(-1)

    def job_kind(self) -> str | None:
        return self._job_kind

    def focus_search(self) -> None:
        self._search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search.selectAll()

    def refresh(self) -> bool:
        if self._external_jobs:
            if not self._claim_job("refresh"):
                return False
            self.refresh_requested.emit()
            return True

        def work() -> None:
            try:
                models, availability = self._catalog.list_models()
                self._emit_job(self._listed.emit, models, availability)
            except EngineError as exc:
                self._emit_job(self._failed.emit, exc)
            except Exception as exc:
                self._emit_job(self._failed.emit, EngineError("backend_unavailable", str(exc)))
            finally:
                self._emit_job(self._job_done.emit)

        return self._start_job(work, "refresh")

    def load_selected(self) -> None:
        model = self.selected_model()
        if model is None or self._session_busy():
            return
        if self._external_jobs:
            if not self._claim_job("load"):
                return
            self.load_requested.emit(model.ref)
            return

        def work() -> None:
            try:
                loaded = self._catalog.load(model.ref)
                self._emit_job(self._loaded.emit, loaded)
            except EngineError as exc:
                self._emit_job(self._failed.emit, exc)
            except Exception as exc:
                self._emit_job(self._failed.emit, EngineError("load_failed", str(exc)))
            finally:
                self._emit_job(self._job_done.emit)

        self._start_job(work, "load")

    def unload_loaded(self) -> None:
        if self._session_busy():
            return
        if self._external_jobs:
            if not self._claim_job("unload"):
                return
            self.unload_requested.emit()
            return

        def work() -> None:
            try:
                self._catalog.unload()
                self._emit_job(self._unloaded.emit)
            except EngineError as exc:
                self._emit_job(self._failed.emit, exc)
            except Exception as exc:
                self._emit_job(self._failed.emit, EngineError("backend_unavailable", str(exc)))
            finally:
                self._emit_job(self._job_done.emit)

        self._start_job(work, "unload")

    def _cancel_load(self) -> None:
        self._stop_btn.setEnabled(False)
        self._stop_btn.setText("Stopping…")
        self.stop_requested.emit()

    def apply_listed(self, models: object, availability: object) -> None:
        self._on_listed(models, availability)

    def apply_loaded(self, model: object) -> None:
        self._on_loaded(model)

    def apply_unloaded(self) -> None:
        self._on_unloaded()

    def apply_failed(self, error: object) -> None:
        self._on_failed(error)

    def finish_job(self) -> None:
        self._on_job_done()

    def set_chat_busy(self, busy: bool) -> None:
        self._chat_busy = busy
        self._sync_actions()

    def sync_from_session(self) -> None:
        self._render_detail()

    def shutdown(self, timeout_ms: int = _SHUTDOWN_WAIT_MS) -> bool:
        self._closing = True
        done = True
        if self._thread.isRunning():
            done = self._thread.wait(timeout_ms)
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        return done and not self._thread.isRunning()

    def take_running_thread(self, parent: QObject | None) -> QThread | None:
        thread = self._thread
        if not thread.isRunning():
            return None
        thread.setParent(parent)
        return thread

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        super().closeEvent(event)

    def reveal_selected(self) -> None:
        model = self.selected_model()
        if model is None or model.path is None:
            return
        self._reveal(model.path)

    def chat_with_selected(self) -> None:
        model = self.selected_model()
        if model is None:
            return
        self.chat_requested.emit(model.ref)

    def _session_busy(self) -> bool:
        return self._chat_busy or self._catalog.status().generating

    def _claim_job(self, kind: str) -> bool:
        with self._job_lock:
            if self._busy or self._closing:
                return False
            if not self._external_jobs and self._thread.isRunning():
                return False
            self._busy = True
            self._job_kind = kind
        self._sync_actions()
        return True

    def _start_job(self, work: Callable[[], None], kind: str) -> bool:
        if not self._claim_job(kind):
            return False
        self._thread.run_work(work)
        return True

    def _emit_job(self, emit: Callable[..., object], *args: object) -> None:
        if self._closing:
            return
        emit(*args)

    def _on_listed(
        self,
        models: object,
        availability: object,
    ) -> None:
        if self._closing:
            return
        self._set_error(None)
        rows = list(models) if isinstance(models, list) else []
        status = availability if isinstance(availability, dict) else {}
        keep = None
        selected = self.selected_model()
        if selected is not None:
            keep = selected.ref.id
        self._models_by_id = {model.ref.id: model for model in rows}
        self._list.clear()

        banners: list[str] = []
        first_id: str | None = None
        for name in _ordered_backend_names(status, rows):
            ok, reason = status.get(name, (True, None))
            if not ok:
                banners.append(reason or f"{_backend_label(name)} unavailable")
            header = QListWidgetItem(_backend_label(name))
            header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = QFont(header.font())
            font.setBold(True)
            header.setFont(font)
            self._list.addItem(header)
            group = [model for model in rows if str(model.ref.backend) == name]
            if not group and not ok:
                placeholder = QListWidgetItem("Unavailable")
                placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
                self._list.addItem(placeholder)
            for model in group:
                item = QListWidgetItem(model.ref.name)
                item.setData(_ID_ROLE, model.ref.id)
                item.setData(_META_ROLE, _format_size(model.size_bytes))
                self._list.addItem(item)
                if first_id is None:
                    first_id = model.ref.id
        self._sync_list_meta()

        if banners:
            self._banner.setText("\n".join(banners))
            self._banner.show()
        else:
            self._banner.clear()
            self._banner.hide()

        target = keep if keep in self._models_by_id else first_id
        self.select_id(target)
        count = len(rows)
        total = sum(model.size_bytes for model in rows)
        self._summary.setText(
            f"{count} local model{'s' if count != 1 else ''} · {_format_size(total)} on disk"
        )
        self._filter_models()
        self._render_detail()
        self.refreshed.emit()

    def _on_loaded(self, _model: object) -> None:
        if self._closing:
            return
        self._set_error(None)
        self._render_detail()

    def _on_unloaded(self) -> None:
        if self._closing:
            return
        self._set_error(None)
        self._render_detail()

    def _on_failed(self, error: object) -> None:
        if self._closing:
            return
        if isinstance(error, EngineError):
            self._set_error(f"{error.code}: {error}")
        else:
            self._set_error(str(error))
        self._render_detail()

    def _on_job_done(self) -> None:
        if self._closing:
            return
        with self._job_lock:
            self._busy = False
            self._job_kind = None
        self._sync_actions()
        self.job_finished.emit()

    def _on_current_item(
        self,
        _current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self._render_detail()

    def _render_detail(self) -> None:
        model = self.selected_model()
        status = self._catalog.status()
        if model is None:
            self._model_name.setText("Select a model")
            self._model_state.setText("Choose a model from the library to inspect it.")
            self._body.clear()
            self._facts.hide()
        else:
            self._model_name.setText(model.ref.name)
            is_loaded = status.loaded is not None and status.loaded.ref == model.ref
            self._model_state.setText(
                "Generating a response"
                if is_loaded and status.generating
                else "Loaded · Ready for a conversation"
                if is_loaded
                else "Available on this device"
                if model.available
                else model.unavailable_reason or "Currently unavailable"
            )
            self._facts.show()
            self._fact_values["BACKEND"].setText(_backend_label(str(model.ref.backend)))
            self._fact_values["DISK SIZE"].setText(_format_size(model.size_bytes))
            self._body.setText(_detail_text(model))
        self._sync_list_meta()
        self._sync_actions()

    def _sync_list_meta(self) -> None:
        status = self._catalog.status()
        loaded_id = None if status.loaded is None else status.loaded.ref.id
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item is None:
                continue
            model_id = item.data(_ID_ROLE)
            if not model_id:
                continue
            model = self._models_by_id.get(str(model_id))
            if model is None:
                continue
            size = _format_size(model.size_bytes)
            if loaded_id == model.ref.id:
                item.setData(_META_ROLE, f"Loaded · {size}")
            else:
                item.setData(_META_ROLE, size)

    def _sync_actions(self) -> None:
        model = self.selected_model()
        status = self._catalog.status()
        has_model = model is not None
        loaded = status.loaded
        busy = self._busy or self._session_busy()
        self._refresh_btn.setEnabled(not self._busy)
        self._refresh_btn.setText("Refreshing…" if self._job_kind == "refresh" else "Refresh")
        self._load_btn.setEnabled(has_model and not busy)
        self._unload_btn.setEnabled(loaded is not None and not busy)
        self._reveal_btn.setEnabled(has_model and model.path is not None and not self._busy)
        self._chat_btn.setEnabled(has_model)
        self._stop_btn.setVisible(self._external_jobs and self._job_kind == "load")
        if self._job_kind != "load":
            self._stop_btn.setEnabled(True)
            self._stop_btn.setText("Cancel loading")
        if self._busy and self._job_kind == "load":
            self._load_btn.setText("Loading…")
        else:
            self._load_btn.setText("Load")

    def _set_error(self, message: str | None) -> None:
        if message:
            self._error.setText(message)
            self._error.show()
        else:
            self._error.clear()
            self._error.hide()


def _detail_text(model: LocalModel) -> str:
    lines = [model.ref.id, ""]
    if model.path is not None:
        lines.append(f"Location\n{model.path}")
    else:
        lines.append(f"Storage managed by {_backend_label(str(model.ref.backend))}.")
    if model.modified_at is not None:
        lines.append(f"Updated {model.modified_at:%b %d, %Y}")
    if model.details:
        lines.append("")
        lines.extend(f"{key}: {value}" for key, value in model.details.items())
    return "\n".join(lines)
