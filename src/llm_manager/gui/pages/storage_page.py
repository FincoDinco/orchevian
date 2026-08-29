import shutil

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from llm_manager.backends import list_all_models
from llm_manager.config import get_config
from llm_manager.format import format_bytes
from llm_manager.gui.widgets import PageHeader, reveal_in_finder

_BACKENDS = [("mlx", "MLX"), ("ollama", "Ollama"), ("gguf", "GGUF")]


class StoragePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        self._header = PageHeader("Storage", actions=[refresh_btn])
        layout.addWidget(self._header)

        body = QVBoxLayout()
        body.setContentsMargins(28, 18, 28, 24)
        body.setSpacing(16)

        self._disk_label = QLabel()
        self._disk_label.setObjectName("sectionLabel")
        body.addWidget(self._disk_label)
        self._disk_bar = QProgressBar()
        self._disk_bar.setTextVisible(True)
        body.addWidget(self._disk_bar)

        breakdown = QLabel("By backend")
        breakdown.setObjectName("sectionLabel")
        body.addWidget(breakdown)

        grid_host = QWidget()
        self._grid = QGridLayout(grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(24)
        self._grid.setVerticalSpacing(8)
        self._grid.setColumnMinimumWidth(0, 90)
        self._grid.setColumnMinimumWidth(1, 70)
        self._grid.setColumnMinimumWidth(2, 90)
        self._grid.setColumnStretch(3, 1)
        for col, heading in enumerate(["Backend", "Models", "Size", "Share of managed"]):
            lbl = QLabel(heading)
            lbl.setObjectName("detailLabel")
            self._grid.addWidget(lbl, 0, col)

        self._rows: dict[str, tuple[QLabel, QLabel, QProgressBar]] = {}
        for r, (key, name) in enumerate(_BACKENDS, start=1):
            self._grid.addWidget(QLabel(name), r, 0)
            count_lbl = QLabel("0")
            size_lbl = QLabel("—")
            bar = QProgressBar()
            bar.setMaximumHeight(14)
            bar.setTextVisible(False)
            self._grid.addWidget(count_lbl, r, 1)
            self._grid.addWidget(size_lbl, r, 2)
            self._grid.addWidget(bar, r, 3)
            self._rows[key] = (count_lbl, size_lbl, bar)
        body.addWidget(grid_host)

        body.addStretch()

        path_row = QHBoxLayout()
        self._path_label = QLabel()
        self._path_label.setObjectName("mono")
        self._path_label.setWordWrap(True)
        path_row.addWidget(self._path_label, 1)
        reveal_btn = QPushButton("Reveal in Finder")
        reveal_btn.clicked.connect(lambda: reveal_in_finder(get_config().model_dir))
        path_row.addWidget(reveal_btn)
        body.addLayout(path_row)

        layout.addLayout(body)

    def refresh(self):
        cfg = get_config()
        models = list_all_models()

        try:
            usage = shutil.disk_usage(str(cfg.model_dir))
            pct = int(usage.used / usage.total * 100) if usage.total else 0
            self._disk_label.setText(
                f"Disk — {format_bytes(usage.used)} used of {format_bytes(usage.total)}"
            )
            self._disk_bar.setMaximum(100)
            self._disk_bar.setValue(pct)
            self._disk_bar.setFormat(f"{pct}% used")
        except OSError:
            self._disk_label.setText("Disk usage unavailable")

        sizes = {k: 0 for k, _ in _BACKENDS}
        counts = {k: 0 for k, _ in _BACKENDS}
        for m in models:
            sizes[m.backend] = sizes.get(m.backend, 0) + m.size
            counts[m.backend] = counts.get(m.backend, 0) + 1
        total_managed = sum(sizes.values()) or 1

        for key, (count_lbl, size_lbl, bar) in self._rows.items():
            count_lbl.setText(str(counts[key]))
            size_lbl.setText(format_bytes(sizes[key]))
            bar.setMaximum(100)
            bar.setValue(int(sizes[key] / total_managed * 100))

        self._header.set_meta(
            f"{len(models)} models · {format_bytes(sum(sizes.values()))} managed"
        )
        self._path_label.setText(str(cfg.model_dir))
