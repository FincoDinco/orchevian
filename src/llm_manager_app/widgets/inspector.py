"""Chat inspector: system prompt, presets, sampling, last-turn metrics, unload offer."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from llm_engine.domain.models import Conversation, GenerationParams

_PRESETS = ("precise", "balanced", "creative")
_UNLOAD_OFFER_MS = 10_000


def _matching_preset(params: GenerationParams) -> str | None:
    for name in _PRESETS:
        if params == GenerationParams.preset(name):
            return name
    return None


class Inspector(QWidget):
    system_prompt_changed = Signal(str)
    unload_requested = Signal()
    restart_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        unload_offer_ms: int = _UNLOAD_OFFER_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("inspector")
        self._cid: int | None = None
        self._saved_prompt = ""
        self._unload_offer_ms = max(0, int(unload_offer_ms))
        self._generating = False
        self._unload_offered = False

        prompt_label = QLabel("System prompt", self)
        self._prompt = QPlainTextEdit(self)
        self._prompt.setObjectName("systemPromptEdit")
        self._prompt.setPlaceholderText("Instructions for this conversation")
        self._prompt.setTabChangesFocus(True)
        self._prompt.setFixedHeight(96)
        self._prompt.textChanged.connect(self._on_prompt_edited)

        self._prompt_timer = QTimer(self)
        self._prompt_timer.setSingleShot(True)
        self._prompt_timer.setInterval(250)
        self._prompt_timer.timeout.connect(self._emit_prompt)

        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(4)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        self._preset_buttons: dict[str, QPushButton] = {}
        for name in _PRESETS:
            btn = QPushButton(name.title(), self)
            btn.setObjectName(f"preset{name.title()}")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._preset_group.addButton(btn)
            self._preset_buttons[name] = btn
            preset_row.addWidget(btn)
            btn.clicked.connect(lambda _checked=False, key=name: self._apply_preset(key))
        preset_row.addStretch(1)

        self._temperature = QDoubleSpinBox(self)
        self._temperature.setObjectName("temperatureSpin")
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(2)
        self._top_p = QDoubleSpinBox(self)
        self._top_p.setObjectName("topPSpin")
        self._top_p.setRange(0.0, 1.0)
        self._top_p.setSingleStep(0.01)
        self._top_p.setDecimals(2)
        self._max_tokens = QSpinBox(self)
        self._max_tokens.setObjectName("maxTokensSpin")
        self._max_tokens.setRange(1, 128_000)
        self._max_tokens.setSingleStep(256)
        for spin in (self._temperature, self._top_p, self._max_tokens):
            spin.valueChanged.connect(self._on_sampling_changed)

        sampling = QFormLayout()
        sampling.setContentsMargins(0, 0, 0, 0)
        sampling.setSpacing(6)
        sampling.addRow("Temperature", self._temperature)
        sampling.addRow("Top-p", self._top_p)
        sampling.addRow("Max tokens", self._max_tokens)

        self._last_turn = QLabel("—", self)
        self._last_turn.setObjectName("lastTurnLabel")
        self._last_turn.setWordWrap(True)

        self._unload = QPushButton("Unload", self)
        self._unload.setObjectName("unloadButton")
        self._unload.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unload.clicked.connect(self._on_unload)
        self._unload.hide()
        self._restart = QPushButton("Restart", self)
        self._restart.setObjectName("restartButton")
        self._restart.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restart.clicked.connect(self._on_restart)
        self._restart.hide()

        stuck = QHBoxLayout()
        stuck.setContentsMargins(0, 0, 0, 0)
        stuck.setSpacing(8)
        stuck.addWidget(self._unload)
        stuck.addWidget(self._restart)
        stuck.addStretch(1)

        self._offer_timer = QTimer(self)
        self._offer_timer.setSingleShot(True)
        self._offer_timer.timeout.connect(self._on_offer_timeout)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(prompt_label)
        layout.addWidget(self._prompt)
        layout.addLayout(preset_row)
        layout.addLayout(sampling)
        layout.addWidget(QLabel("Last turn", self))
        layout.addWidget(self._last_turn)
        layout.addLayout(stuck)
        layout.addStretch(1)

        self._set_params(GenerationParams.preset("balanced"))
        self.setEnabled(False)

    def set_unload_offer_ms(self, ms: int) -> None:
        self._unload_offer_ms = max(0, int(ms))

    def params(self) -> GenerationParams:
        return GenerationParams(
            temperature=float(self._temperature.value()),
            top_p=float(self._top_p.value()),
            max_tokens=int(self._max_tokens.value()),
        )

    def set_conversation(self, conversation: Conversation | None) -> None:
        self._prompt_timer.stop()
        if conversation is None:
            self._cid = None
            self._saved_prompt = ""
            blocked = self._prompt.blockSignals(True)
            self._prompt.clear()
            self._prompt.blockSignals(blocked)
            self.set_last_turn(chunks=None, elapsed=None, tps=None)
            self.set_generating(False)
            self.setEnabled(False)
            return
        self._cid = conversation.summary.id
        self._saved_prompt = conversation.system_prompt
        blocked = self._prompt.blockSignals(True)
        self._prompt.setPlainText(conversation.system_prompt)
        self._prompt.blockSignals(blocked)
        last = next((m for m in reversed(conversation.messages) if m.role == "assistant"), None)
        if last is not None:
            self.set_last_turn(chunks=None, elapsed=last.elapsed_s, tps=last.tokens_per_sec)
        else:
            self.set_last_turn(chunks=None, elapsed=None, tps=None)
        self.setEnabled(True)

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        self._offer_timer.stop()
        if not generating:
            self._unload_offered = False
            self._unload.hide()
            self._restart.hide()
            return
        self._unload.hide()
        self._restart.hide()
        self._unload_offered = False
        self._offer_timer.start(self._unload_offer_ms)

    def set_last_turn(
        self,
        *,
        chunks: int | None,
        elapsed: float | None,
        tps: float | None,
    ) -> None:
        parts: list[str] = []
        if tps is not None:
            parts.append(f"{tps:.1f} chunks/s")
        if elapsed is not None:
            parts.append(f"{elapsed:.1f} s")
        if chunks is not None:
            parts.append(f"{chunks} chunks")
        self._last_turn.setText(" · ".join(parts) if parts else "—")

    def show_restart(self) -> None:
        if self._generating:
            self._restart.show()

    def _apply_preset(self, name: str) -> None:
        self._set_params(GenerationParams.preset(name))

    def _set_params(self, params: GenerationParams) -> None:
        widgets = (self._temperature, self._top_p, self._max_tokens)
        blocked = [w.blockSignals(True) for w in widgets]
        self._temperature.setValue(params.temperature)
        self._top_p.setValue(params.top_p)
        self._max_tokens.setValue(params.max_tokens)
        for widget, was in zip(widgets, blocked, strict=True):
            widget.blockSignals(was)
        self._sync_preset_buttons()

    def _sync_preset_buttons(self) -> None:
        match = _matching_preset(self.params())
        # Exclusive groups refuse to uncheck the last button; drop that for custom values.
        self._preset_group.setExclusive(False)
        for name, btn in self._preset_buttons.items():
            blocked = btn.blockSignals(True)
            btn.setChecked(name == match)
            btn.blockSignals(blocked)
        self._preset_group.setExclusive(match is not None)

    def _on_sampling_changed(self, _value: float) -> None:
        self._sync_preset_buttons()

    def _on_prompt_edited(self) -> None:
        self._prompt_timer.start()

    def _emit_prompt(self) -> None:
        if self._cid is None:
            return
        text = self._prompt.toPlainText()
        if text == self._saved_prompt:
            return
        self._saved_prompt = text
        self.system_prompt_changed.emit(text)

    def _on_offer_timeout(self) -> None:
        if not self._generating:
            return
        self._unload_offered = True
        self._unload.show()

    def _on_unload(self) -> None:
        self.unload_requested.emit()
        if self._generating:
            self._restart.show()

    def _on_restart(self) -> None:
        self.restart_requested.emit()
