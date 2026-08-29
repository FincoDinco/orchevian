from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from llm_manager import api_server
from llm_manager.backends import get_backend, list_all_models
from llm_manager.config import get_config
from llm_manager.gui.widgets import PageHeader, StatusBadge


class ServerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._models = []
        self._build_ui()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._update_status)
        self._poll_timer.start()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = PageHeader("API Server")
        layout.addWidget(self._header)

        body = QVBoxLayout()
        body.setContentsMargins(28, 18, 28, 24)
        body.setSpacing(16)

        subtitle = QLabel(
            "Expose an OpenAI-compatible endpoint so any app that accepts a custom "
            "base URL can talk to your local models."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        body.addWidget(subtitle)

        top = QHBoxLayout()
        top.setSpacing(16)
        top.addWidget(self._build_endpoint_group(), 1)
        top.addWidget(self._build_connect_group(), 1)
        body.addLayout(top)

        log_group = QGroupBox("Recent requests")
        log_layout = QVBoxLayout(log_group)
        self._log_text = QTextEdit(readOnly=True)
        self._log_text.setObjectName("mono")
        self._log_text.setPlaceholderText("Requests will appear here once the server is running.")
        self._log_text.setMinimumHeight(150)
        log_layout.addWidget(self._log_text)
        body.addWidget(log_group)
        body.addStretch()

        layout.addLayout(body)
        self._update_status()

    def _build_endpoint_group(self) -> QGroupBox:
        cfg = get_config()
        group = QGroupBox("Endpoint")
        form = QFormLayout(group)

        self._host_edit = QLineEdit(cfg.api_host)
        form.addRow("Host", self._host_edit)
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(cfg.api_port)
        form.addRow("Port", self._port_spin)

        self._model_combo = QComboBox()
        self._refresh_models()
        form.addRow("Model", self._model_combo)
        refresh_btn = QPushButton("Refresh models")
        refresh_btn.setObjectName("quiet")
        refresh_btn.clicked.connect(self._refresh_models)
        form.addRow("", refresh_btn)

        control_row = QHBoxLayout()
        self._status_badge = StatusBadge()
        control_row.addWidget(self._status_badge)
        control_row.addStretch()
        self._toggle_btn = QPushButton("Start server")
        self._toggle_btn.setObjectName("primary")
        self._toggle_btn.clicked.connect(self._toggle_server)
        control_row.addWidget(self._toggle_btn)
        form.addRow("", self._wrap(control_row))
        return group

    def _build_connect_group(self) -> QGroupBox:
        group = QGroupBox("Connect")
        v = QVBoxLayout(group)
        self._endpoint_label = QLabel("—")
        self._endpoint_label.setObjectName("mono")
        self._endpoint_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self._endpoint_label)
        self._example_text = QTextEdit(readOnly=True)
        self._example_text.setObjectName("mono")
        self._example_text.setPlaceholderText("Start the server to see a ready-to-run request.")
        self._example_text.setMinimumHeight(120)
        v.addWidget(self._example_text)
        v.addStretch()
        return group

    def _wrap(self, layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    def _refresh_models(self):
        self._models = list_all_models()
        self._model_combo.clear()
        for m in self._models:
            self._model_combo.addItem(f"{m.name}  ·  {m.display_backend}")

    def _toggle_server(self):
        if api_server.get_status()["running"]:
            api_server.stop()
        else:
            idx = self._model_combo.currentIndex()
            if 0 <= idx < len(self._models):
                model = self._models[idx]
                api_server.set_active_model(model, get_backend(model.backend))
            api_server.start(host=self._host_edit.text().strip(), port=self._port_spin.value())
        self._update_status()

    def _update_status(self):
        status = api_server.get_status()
        base = f"http://{self._host_edit.text()}:{self._port_spin.value()}"

        if status["running"]:
            self._status_badge.set_state("running")
            self._toggle_btn.setText("Stop server")
            self._header.set_meta(f"running · {base}/v1")
            self._endpoint_label.setText(f"{base}/v1")
            self._example_text.setPlainText(
                f"curl {base}/v1/chat/completions \\\n"
                f'  -H "Content-Type: application/json" \\\n'
                f'  -d \'{{"model": "local", "messages": '
                f'[{{"role": "user", "content": "Hello"}}]}}\''
            )
        else:
            self._status_badge.set_state("stopped")
            self._toggle_btn.setText("Start server")
            self._header.set_meta("stopped")
            self._endpoint_label.setText("—")
            self._example_text.setPlainText("")

        log = api_server.get_request_log()
        if log:
            lines = [
                f"[{r['time']}] {r['model']} — {r['messages']} messages "
                f"{'(stream)' if r.get('stream') else ''}"
                for r in reversed(log[-10:])
            ]
            self._log_text.setPlainText("\n".join(lines))
