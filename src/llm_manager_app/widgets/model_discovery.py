"""A guided model picker; network work is owned by ModelsView."""

from PySide6.QtCore import QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from llm_engine.services.discovery import GIB, Hardware, RemoteModel
from llm_engine.services.downloads import DownloadPlan
from llm_manager_app.model_names import friendly_name
from llm_manager_app.tokens import current_palette, qcolor

_META_ROLE = Qt.ItemDataRole.UserRole + 1


def _display_name(model: RemoteModel) -> str:
    return friendly_name(model.repo_id)


class _ModelDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(180, 72)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = current_palette()
        rect = option.rect.adjusted(0, 3, -1, -3)
        active = option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor(palette.selection))
            painter.drawRoundedRect(rect, palette.radius_control, palette.radius_control)
        font = QFont(option.font)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(palette.text))
        title = painter.fontMetrics().elidedText(
            index.data(), Qt.TextElideMode.ElideRight, max(0, rect.width() - 32),
        )
        painter.drawText(QRect(rect.x() + 16, rect.y() + 12, rect.width() - 32, 22), title)
        painter.setFont(option.font)
        painter.setPen(QColor(palette.secondary))
        meta = painter.fontMetrics().elidedText(
            index.data(_META_ROLE), Qt.TextElideMode.ElideRight, max(0, rect.width() - 32),
        )
        painter.drawText(QRect(rect.x() + 16, rect.y() + 39, rect.width() - 32, 22), meta)
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QColor(palette.accent))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
        painter.restore()


class ModelDiscovery(QWidget):
    search_requested = Signal(str, str, bool, float)
    files_requested = Signal(object, str)
    download_requested = Signal(object, object, str)
    downloads_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._transfer_state = ""
        self._plan: DownloadPlan | None = None
        self._hardware: Hardware | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        controls = QHBoxLayout()
        self.query = QLineEdit(self)
        self.query.setPlaceholderText("Search models by name")
        self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName("Search models")
        self.search = QPushButton("Search", self)
        self.search.clicked.connect(self._search)
        self.query.returnPressed.connect(self._search)
        controls.addWidget(self.query, 1)
        controls.addWidget(self.search)
        layout.addLayout(controls)

        filters = QHBoxLayout()
        self.recommended = QCheckBox("Suggested for my computer", self)
        self.recommended.setChecked(True)
        self.recommended.setToolTip("Show models estimated to fit your computer’s memory.")
        self.recommended.toggled.connect(self._search)
        self.options = QPushButton("Options…", self)
        self.options.clicked.connect(self._show_options)
        filters.addWidget(self.recommended)
        filters.addStretch(1)
        filters.addWidget(self.options)
        layout.addLayout(filters)

        self.message = QLabel("Finding models that should fit your computer…", self)
        self.message.setObjectName("pageSubtitle")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)
        self.results = QListWidget(self)
        self.results.setObjectName("discoveryResults")
        self.results.setAccessibleName("Available models")
        self.results.setItemDelegate(_ModelDelegate(self.results))
        self.results.setMouseTracking(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.currentItemChanged.connect(self._selection_changed)
        layout.addWidget(self.results, 1)

        self.selection = QWidget(self)
        footer = QVBoxLayout(self.selection)
        footer.setContentsMargins(0, 8, 0, 0)
        footer.setSpacing(10)
        self.selection_title = QLabel(self)
        self.selection_title.setObjectName("chatTitle")
        self.selection_title.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_title.setWordWrap(True)
        self.selection_hint = QLabel(self)
        self.selection_hint.setObjectName("pageSubtitle")
        self.selection_hint.setWordWrap(True)
        footer.addWidget(self.selection_title)
        footer.addWidget(self.selection_hint)
        self.choices = QComboBox(self)
        self.choices.setAccessibleName("Download version")
        self.choices.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.choices.setMinimumContentsLength(12)
        self.choices.currentIndexChanged.connect(self._update_choice)
        self.choices.hide()
        footer.addWidget(self.choices)
        actions = QHBoxLayout()
        self.model_details = QPushButton("Model details", self)
        self.model_details.clicked.connect(lambda: self._details_dialog.exec())
        self.change_version = QPushButton("Change version", self)
        self.change_version.setCheckable(True)
        self.change_version.toggled.connect(self.choices.setVisible)
        self.change_version.hide()
        self.files = QPushButton("Get model", self)
        self.files.setObjectName("primaryButton")
        self.files.clicked.connect(self._request_files)
        self.download = QPushButton("Download", self)
        self.download.setObjectName("primaryButton")
        self.download.clicked.connect(self._request_download)
        actions.addWidget(self.model_details)
        actions.addWidget(self.change_version)
        actions.addStretch(1)
        actions.addWidget(self.files)
        actions.addWidget(self.download)
        footer.addLayout(actions)
        layout.addWidget(self.selection)

        self._options_dialog = QDialog(self)
        self._options_dialog.setWindowTitle("Model search options")
        self._options_dialog.setMinimumWidth(440)
        settings = QVBoxLayout(self._options_dialog)
        hint = QLabel("Automatic settings are a good place to start.", self._options_dialog)
        hint.setWordWrap(True)
        settings.addWidget(hint)
        form = QFormLayout()
        self.format = QComboBox(self._options_dialog)
        self.format.addItem("Automatic (recommended)", "auto")
        self.format.addItem("GGUF", "gguf")
        self.format.addItem("MLX (Apple Silicon)", "mlx")
        self.budget = QDoubleSpinBox(self._options_dialog)
        self.budget.setRange(0, 65536)
        self.budget.setDecimals(1)
        self.budget.setSpecialValueText("Automatic")
        self.budget.setSuffix(" GB")
        self.budget.setToolTip("Optional limit on the system memory used for recommendations.")
        form.addRow("Model format", self.format)
        form.addRow("Memory limit", self.budget)
        settings.addLayout(form)
        self.token = QLineEdit(self._options_dialog)
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Hugging Face read token (optional)")
        self.token.setAccessibleName("Hugging Face read token")
        settings.addWidget(self.token)
        token_hint = QLabel(
            "Only needed for models that require access. Kept for this app session.",
            self._options_dialog,
        )
        token_hint.setWordWrap(True)
        settings.addWidget(token_hint)
        self.hardware = QLabel("Computer details will appear after a search.", self._options_dialog)
        self.hardware.setWordWrap(True)
        self.hardware.setTextFormat(Qt.TextFormat.PlainText)
        settings.addWidget(self.hardware)
        settings_done = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        settings_done.rejected.connect(self._options_dialog.accept)
        settings.addWidget(settings_done)

        self._details_dialog = QDialog(self)
        self._details_dialog.setWindowTitle("Model details")
        self._details_dialog.setMinimumWidth(440)
        detail_layout = QVBoxLayout(self._details_dialog)
        self.details = QLabel(self._details_dialog)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.details)
        self.open = QPushButton("View on Hugging Face", self._details_dialog)
        self.open.clicked.connect(self.open_selected)
        detail_layout.addWidget(self.open)
        close_details = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_details.rejected.connect(self._details_dialog.accept)
        detail_layout.addWidget(close_details)
        self._selection_changed()

    def _show_options(self) -> None:
        previous = (self.format.currentData(), self.budget.value())
        self._options_dialog.exec()
        if previous != (self.format.currentData(), self.budget.value()):
            self._search()

    def _search(self, *_args: object) -> None:
        self.request(self.recommended.isChecked())

    def request(self, recommended: bool) -> None:
        if self._busy:
            return
        self.search_requested.emit(
            self.query.text(), self.format.currentData(), recommended, self.budget.value()
        )

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self.query, self.format, self.search, self.recommended, self.budget,
            self.results, self.token, self.choices, self.options, self.change_version,
        ):
            widget.setEnabled(not busy)
        self.files.setEnabled(not busy and self.results.currentItem() is not None)
        self.download.setEnabled(not busy and self._plan is not None)
        self.files.setVisible(self._plan is None)
        self.download.setVisible(self._plan is not None)

    def _request_files(self) -> None:
        item = self.results.currentItem()
        if item is not None and not self._busy:
            self.files_requested.emit(item.data(Qt.ItemDataRole.UserRole), self.token.text())

    def apply_plan(self, plan: DownloadPlan) -> None:
        self._plan = plan
        self.choices.clear()
        for choice in plan.choices:
            self.choices.addItem(f"{choice.name} · {choice.size_bytes / GIB:.2f} GB", choice)
            self.choices.setItemData(
                self.choices.count() - 1, self.choices.itemText(self.choices.count() - 1),
                Qt.ItemDataRole.ToolTipRole,
            )
        self.change_version.setVisible(len(plan.choices) > 1)
        self.message.setText("Ready to download. Your model will appear in Downloaded.")
        self._update_choice()
        self.set_busy(self._busy)

    def _update_choice(self, *_args: object) -> None:
        choice = self.choices.currentData()
        if choice is None:
            return
        self.download.setText(f"Download · {choice.size_bytes / GIB:.2f} GB")
        self.selection_hint.setText("Saves to your computer. You can cancel at any time.")

    def _request_download(self) -> None:
        if self._transfer_state in {"Queued", "Downloading", "Cancelling", "Complete"}:
            self.downloads_requested.emit()
            return
        choice = self.choices.currentData()
        if self._plan is not None and choice is not None and not self._busy:
            self.download_requested.emit(self._plan, choice, self.token.text())

    def update_downloads(self, jobs) -> None:
        self._transfer_state = ""
        if self._plan is None or self.choices.currentData() is None:
            return
        choice = self.choices.currentData()
        key = (self._plan.model.repo_id, self._plan.model.format, choice.name)
        for job in jobs:
            if job.key == key:
                self._transfer_state = job.state
                break
        if self._transfer_state in {"Queued", "Downloading", "Cancelling", "Complete"}:
            self.download.setText("View downloads")
            self.selection_hint.setText(
                "Ready in Downloads." if self._transfer_state == "Complete"
                else "Downloading in the background. Keep browsing for another model."
            )
        else:
            self._update_choice()

    def apply_results(
        self, models: list[RemoteModel], hardware: Hardware, recommended: bool,
    ) -> None:
        self._hardware = hardware
        self.hardware.setText(hardware.summary)
        self.results.clear()
        if models:
            message = (
                "Suggested for your computer · Memory needs are estimates."
                if recommended else f"{len(models)} models · Check memory needs before downloading."
            )
        elif recommended:
            message = (
                "No suggestions found. Try another name or turn off Suggested for my computer."
            )
        else:
            message = "No models found. Try another name."
        self.message.setText(message)
        for model in models:
            fit = model.fit(hardware)
            fit_label = {
                "Likely fits": "Should fit your computer",
                "Exceeds suggested memory budget": "May need more memory",
                "Requires Apple Silicon": "Needs an Apple Silicon Mac",
            }.get(fit, fit)
            size = (
                f"About {model.estimated_bytes / GIB:.1f} GB memory"
                if model.estimated_bytes is not None else "Memory needs unknown"
            )
            item = QListWidgetItem(_display_name(model))
            item.setData(Qt.ItemDataRole.UserRole, model)
            item.setData(_META_ROLE, f"{size} · {fit_label}")
            item.setToolTip(model.repo_id + "\n" + model.execution_hint(hardware))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{item.text()}. {size}. {fit_label}.")
            self.results.addItem(item)
        if models:
            self.results.setCurrentRow(0)

    def _selection_changed(self, *_args: object) -> None:
        self._plan = None
        self._transfer_state = ""
        self.choices.clear()
        self.change_version.setChecked(False)
        self.change_version.hide()
        item = self.results.currentItem()
        model = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.selection.setVisible(model is not None)
        self.open.setEnabled(model is not None)
        self.selection_title.setText(_display_name(model) if model else "")
        self.selection_hint.setText(
            "Requires access on Hugging Face. Open Model details to learn more."
            if model and model.gated else "See the download size before installing."
        )
        self.details.setText(
            f"{model.repo_id}\n\n{model.format.upper()}\n{model.estimate_basis}\n\n"
            "Memory use varies with conversation length and other running apps. "
            "A memory estimate does not guarantee compatibility or speed." + (
                "\n\nAccept access on Hugging Face, then add a read token in Options."
                if model.gated else ""
            ) if model else ""
        )
        self.set_busy(self._busy)

    def open_selected(self, *_args: object) -> None:
        item = self.results.currentItem()
        if item is not None:
            QDesktopServices.openUrl(QUrl(item.data(Qt.ItemDataRole.UserRole).url))
