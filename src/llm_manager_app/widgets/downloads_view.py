"""Independent download jobs and their persistent, nonmodal workspace."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import ModelRef
from llm_engine.services.discovery import GIB
from llm_engine.services.downloads import DownloadChoice, DownloadPlan, DownloadService
from llm_manager_app.icons import icon
from llm_manager_app.model_names import friendly_name

_ACTIVE = {"Queued", "Downloading", "Cancelling"}


@dataclass
class DownloadJob:
    id: int
    plan: DownloadPlan
    choice: DownloadChoice
    token: str = field(repr=False, default="")
    state: str = "Queued"
    done: int = 0
    total: int = 0
    message: str = "Waiting for a download slot"
    ref: ModelRef | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.plan.model.repo_id, self.plan.model.format, self.choice.name


class _DownloadThread(QThread):
    progressed = Signal(int, object, object, str)

    def __init__(self, service: DownloadService, job: DownloadJob, parent: QObject) -> None:
        super().__init__(parent)
        self.service, self.job = service, job
        self.ref: ModelRef | None = None
        self.error: EngineError | None = None

    def run(self) -> None:
        last_update = 0.0

        def progress(done: int, total: int, name: str) -> None:
            nonlocal last_update
            now = time.monotonic()
            if now - last_update >= 0.1 or done == total:
                self.progressed.emit(self.job.id, done, total, name)
                last_update = now

        try:
            self.ref = self.service.download(
                self.job.plan, self.job.choice, self.job.cancel, progress, self.job.token,
            )
        except EngineError as exc:
            self.error = exc
        except Exception:
            self.error = EngineError("download_failed", "Could not download this model. Retry.")


class DownloadManager(QObject):
    changed = Signal()
    installed = Signal(object)

    def __init__(self, parent: QObject | None = None, *, parallel: int = 3) -> None:
        super().__init__(parent)
        self.jobs: dict[int, DownloadJob] = {}
        self._threads: dict[int, _DownloadThread] = {}
        self._services: dict[int, DownloadService] = {}
        self._parallel = max(1, parallel)
        self._closing = False

    @property
    def active_count(self) -> int:
        return sum(job.state in _ACTIVE for job in self.jobs.values())

    def add(self, service: DownloadService, plan: DownloadPlan, choice: DownloadChoice,
            token: str = "") -> DownloadJob:
        key = (plan.model.repo_id, plan.model.format, choice.name)
        for job in self.jobs.values():
            if job.key == key:
                if job.state in {"Failed", "Cancelled"}:
                    job.plan, job.choice, job.token = plan, choice, token
                    self.retry(job.id)
                return job
        job = DownloadJob(len(self.jobs) + 1, plan, choice, token)
        self.jobs[job.id] = job
        self._services[job.id] = service
        self._start_pending()
        self.changed.emit()
        return job

    def _start_pending(self) -> None:
        if self._closing:
            return
        for job in self.jobs.values():
            if len(self._threads) >= self._parallel:
                break
            if job.state != "Queued":
                continue
            job.state, job.message = "Downloading", "Starting download…"
            thread = _DownloadThread(self._services[job.id], job, self)
            self._threads[job.id] = thread
            thread.progressed.connect(self._progress, Qt.ConnectionType.QueuedConnection)
            thread.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
            thread.start()

    def _progress(self, job_id: int, done: int, total: int, name: str) -> None:
        job = self.jobs[job_id]
        job.done = done
        job.total = total
        if job.state == "Downloading":
            job.message = (f"{done / GIB:.2f} of {total / GIB:.2f} GB"
                           if total else name)
        self.changed.emit()

    def _finished(self) -> None:
        thread = self.sender()
        if not isinstance(thread, _DownloadThread):
            return
        job = thread.job
        self._threads.pop(job.id, None)
        if thread.ref is not None:
            job.ref, job.state, job.message = thread.ref, "Complete", "Ready in Installed"
            job.done = job.total or job.choice.size_bytes
            job.token = ""
            if not self._closing:
                self.installed.emit(thread.ref)
        elif thread.error is not None:
            job.state = "Cancelled" if thread.error.code == "cancelled" else "Failed"
            job.message = str(thread.error)
        thread.deleteLater()
        self._start_pending()
        self.changed.emit()

    def cancel(self, job_id: int) -> None:
        job = self.jobs[job_id]
        if job.state not in _ACTIVE:
            return
        job.cancel.set()
        if job.state == "Queued":
            job.state, job.message = "Cancelled", "Download cancelled"
        else:
            job.state, job.message = "Cancelling", (
                "Stopping Ollama download…" if job.plan.model.format == "ollama"
                else "Removing partial files…"
            )
        self.changed.emit()

    def retry(self, job_id: int) -> None:
        job = self.jobs[job_id]
        if self._closing or job.state not in {"Failed", "Cancelled"}:
            return
        job.cancel = threading.Event()
        job.done = 0
        job.total = 0
        job.state, job.message = "Queued", "Waiting for a download slot"
        self._start_pending()
        self.changed.emit()

    def shutdown(self, timeout_ms: int) -> bool:
        self._closing = True
        for job in self.jobs.values():
            if job.state in _ACTIVE:
                self.cancel(job.id)
        deadline = time.monotonic() + timeout_ms / 1000
        for thread in self._threads.values():
            thread.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        return not any(thread.isRunning() for thread in self._threads.values())

    def take_running_threads(self, parent: QObject | None) -> list[QThread]:
        running = [thread for thread in self._threads.values() if thread.isRunning()]
        for thread in running:
            thread.setParent(parent)
        return running


class DownloadsView(QWidget):
    open_model = Signal(object)
    browse_requested = Signal()
    close_requested = Signal()
    details_changed = Signal()

    def __init__(self, manager: DownloadManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._rows: dict[int, tuple[QLabel, QProgressBar, QPushButton]] = {}
        self._details: dict[int, QWidget] = {}
        self._disclosures: dict[int, QToolButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(12)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Downloads", self)
        title.setObjectName("chatTitle")
        self.summary = QLabel(self)
        self.summary.setObjectName("pageSubtitle")
        heading.addWidget(title)
        heading.addWidget(self.summary)
        header.addLayout(heading, 1)
        close = QToolButton(self)
        close.setIcon(icon("close"))
        close.setAutoRaise(True)
        close.setToolTip("Close Downloads")
        close.setAccessibleName("Close Downloads")
        close.clicked.connect(self.close_requested.emit)
        header.addWidget(close)
        layout.addLayout(header)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget(scroll)
        self._list = QVBoxLayout(body)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        self._list.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        browse = QPushButton("Find Models…", self)
        browse.clicked.connect(self.browse_requested.emit)
        layout.addWidget(browse, 0, Qt.AlignmentFlag.AlignRight)
        manager.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        counts = {state: sum(job.state == state for job in self.manager.jobs.values())
                  for state in (*_ACTIVE, "Complete", "Failed", "Cancelled")}
        summary = [f"{counts[state]} {label}" for state, label in (
            ("Downloading", "downloading"), ("Queued", "queued"),
            ("Cancelling", "cancelling"), ("Complete", "complete"),
            ("Failed", "failed"), ("Cancelled", "cancelled"),
        ) if counts[state]]
        self.summary.setText(" · ".join(summary) if summary
                             else "No downloads yet. Find a model to get started.")
        for job in self.manager.jobs.values():
            if job.id not in self._rows:
                card = QWidget(self)
                card.setObjectName("downloadCard")
                card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(0, 12, 0, 12)
                row = QHBoxLayout()
                row.setSpacing(8)
                disclosure = QToolButton(card)
                disclosure.setAutoRaise(True)
                disclosure.setCheckable(True)
                disclosure.setIcon(icon("chevron-right"))
                disclosure.setIconSize(QSize(12, 12))
                disclosure.setAccessibleName(f"Details for {job.plan.model.repo_id}")
                disclosure.setToolTip("Show download details")
                row.addWidget(disclosure)
                text = QVBoxLayout()
                name = QLabel(friendly_name(job.plan.model.repo_id), card)
                name.setObjectName("chatTitle")
                name.setTextFormat(Qt.TextFormat.PlainText)
                name.setWordWrap(True)
                status = QLabel(card)
                status.setObjectName("pageSubtitle")
                status.setTextFormat(Qt.TextFormat.PlainText)
                status.setWordWrap(True)
                progress = QProgressBar(card)
                progress.setRange(0, 1000)
                progress.setTextVisible(False)
                progress.setAccessibleName(f"Download progress for {job.plan.model.repo_id}")
                text.addWidget(name)
                text.addWidget(status)
                text.addWidget(progress)
                row.addLayout(text, 1)
                action = QPushButton(card)
                action.clicked.connect(lambda checked=False, ident=job.id: self._act(ident))
                row.addWidget(action)
                card_layout.addLayout(row)
                details = QLabel(
                    f"{job.plan.model.repo_id}\n{job.choice.name}\n"
                    + ("Managed by Ollama · size reported during download"
                       if job.plan.model.format == "ollama" else
                       f"{job.choice.size_bytes / GIB:.2f} GB · {len(job.choice.files)} "
                       f"file{'s' if len(job.choice.files) != 1 else ''}"), card
                )
                details.setObjectName("downloadDetails")
                details.setTextFormat(Qt.TextFormat.PlainText)
                details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                details.setWordWrap(True)
                details.hide()
                card_layout.addWidget(details)
                disclosure.toggled.connect(details.setVisible)
                disclosure.toggled.connect(
                    lambda opened, button=disclosure: self._disclose(button, opened)
                )
                self._details[job.id] = details
                self._disclosures[job.id] = disclosure
                self._list.insertWidget(self._list.count() - 1, card)
                self._rows[job.id] = status, progress, action
            status, progress, action = self._rows[job.id]
            status.setText(job.message if job.state == "Downloading" else job.state)
            self._details[job.id].setToolTip(job.message)
            if job.state == "Failed":
                status.setText(f"Failed · {job.message}")
            total = job.total or job.choice.size_bytes
            indeterminate = job.state == "Cancelling" or not total
            progress.setRange(0, 0 if indeterminate else 1000)
            if not indeterminate:
                progress.setValue(min(1000, int(1000 * job.done / total)))
            progress.setVisible(job.state in {"Downloading", "Cancelling"})
            progress.setAccessibleDescription(status.text())
            action.setText("Open model" if job.state == "Complete" else
                           "Retry" if job.state in {"Failed", "Cancelled"} else "Cancel")
            action.setAccessibleName(
                f"{action.text()}: {job.plan.model.repo_id}, {job.choice.name}"
            )
            action.setEnabled(job.state != "Cancelling")

    def _disclose(self, button: QToolButton, opened: bool) -> None:
        button.setIcon(icon("chevron-down" if opened else "chevron-right"))
        button.setToolTip("Hide download details" if opened else "Show download details")
        self.details_changed.emit()

    def _act(self, job_id: int) -> None:
        job = self.manager.jobs[job_id]
        if job.state == "Complete":
            self.open_model.emit(job.ref)
        elif job.state in {"Failed", "Cancelled"}:
            self.manager.retry(job_id)
        else:
            self.manager.cancel(job_id)


class DownloadsPopover(QFrame):
    dismissed = Signal()

    def __init__(self, view: DownloadsView, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay, True)
        self.setObjectName("downloadsPopover")
        self.setWindowTitle("Downloads")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._view = view
        self._anchor: QWidget | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        view.show()
        view.close_requested.connect(self.hide)
        view.details_changed.connect(self._resize_to_content)
        view.manager.changed.connect(self._resize_to_content)

    def show_for(self, anchor: QWidget) -> None:
        self._anchor = anchor
        self._resize_to_content()
        self.show()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def _resize_to_content(self) -> None:
        anchor = self._anchor
        if anchor is None:
            return
        screen = anchor.screen().availableGeometry()
        height = 152 + sum(status.parentWidget().sizeHint().height()
                           for status, _, _ in self._view._rows.values())
        self.resize(min(460, screen.width()), min(max(180, height), 480, screen.height()))
        point = anchor.mapToGlobal(QPoint(anchor.width() - self.width(), anchor.height() + 6))
        point.setX(max(screen.left(), min(point.x(), screen.right() - self.width() + 1)))
        point.setY(max(screen.top(), min(point.y(), screen.bottom() - self.height() + 1)))
        self.move(point)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.dismissed.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)
