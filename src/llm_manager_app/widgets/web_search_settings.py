"""Settings → Web Search: optional search-service keys, explained for newcomers."""

from __future__ import annotations

import threading

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from llm_engine.services.web_retrieval import SEARCH_SERVICES, check_search_key


def _setting(service_id: str) -> str:
    return f"web_search/{service_id}_key"


def load_search_keys(settings: QSettings) -> list[tuple[str, str]]:
    """Saved keys in the order they are tried."""
    keys = []
    for service in SEARCH_SERVICES:
        key = str(settings.value(_setting(service.id), "") or "").strip()
        if key:
            keys.append((service.id, key))
    return keys


INTRO = (
    "You don't need to set anything up here. Web search already works for free using Exa's "
    "free search. It has a daily limit, so if you search a lot and it starts saying it is "
    "busy, you can add your own free key below for a bigger allowance."
)
PRIVACY = (
    "<b>Privacy:</b> when Web search is on, the search service receives your question (up to "
    "500 characters) and may keep it under its own privacy policy. Your files and saved chats "
    "are never sent."
)
WHAT_IS_A_KEY = (
    "<b>What is an API key?</b> It works like a password that lets Orchevian search with your "
    "free account on that service. It stays on this computer and is only sent to that service."
)
STEPS = (
    "<b>How to add one</b><br>"
    "1. Pick a service below. Exa is the easiest place to start.<br>"
    "2. Click <i>Get a free key</i> and create an account. Email or Google sign-in works.<br>"
    "3. On their website, find your API key and copy it.<br>"
    "4. Paste it into the box and click <i>Test</i>."
)
ORDER_NOTE = (
    "Added more than one? Orchevian uses them from top to bottom and moves on if one runs "
    "out. After your keys it uses Exa's free search."
)


class _ServiceCard(QFrame):
    def __init__(self, service, settings: QSettings, recommended: bool, parent=None):
        super().__init__(parent)
        self.service = service
        self._settings = settings
        self.setObjectName("searchServiceCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        name = QLabel(service.name, self)
        name.setObjectName("searchServiceName")
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(name)
        if recommended:
            badge = QLabel("Recommended", self)
            badge.setObjectName("searchServiceBadge")
            header.addWidget(badge)
        header.addStretch(1)
        get_key = QPushButton("Get a free key ↗", self)
        get_key.setObjectName("getSearchKeyButton")
        get_key.setToolTip(f"Opens {service.signup_url} in your browser")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(service.signup_url)))
        header.addWidget(get_key)

        plan = service.free_plan + (
            " · Needs a credit card on file" if service.needs_card else " · No credit card"
        )
        details = QLabel(plan, self)
        details.setObjectName("settingsHint")

        self.key = QLineEdit(self)
        self.key.setObjectName(f"{service.id}KeyEdit")
        self.key.setPlaceholderText("Paste your key here")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setAccessibleName(f"{service.name} API key")
        self.key.setText(str(settings.value(_setting(service.id), "") or ""))
        self.test = QPushButton("Test", self)
        self.test.setObjectName(f"{service.id}TestButton")
        self.remove = QPushButton("Remove", self)
        self.remove.setObjectName(f"{service.id}RemoveButton")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.key, 1)
        row.addWidget(self.test)
        row.addWidget(self.remove)

        self.status = QLabel(self)
        self.status.setObjectName("settingsHint")
        self.status.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        layout.addLayout(header)
        layout.addWidget(details)
        layout.addLayout(row)
        layout.addWidget(self.status)
        self.show_saved()

    def value(self) -> str:
        return self.key.text().strip()

    def show_saved(self) -> None:
        has_key = bool(self.value())
        self.remove.setEnabled(has_key)
        self.test.setEnabled(has_key)
        self.status.setText("Saved. Click Test to make sure it works." if has_key
                            else "Not set up.")

    def show_result(self, error: str) -> None:
        self.test.setEnabled(bool(self.value()))
        self.test.setText("Test")
        # Already in Settings: point at the likely cause instead of back here.
        error = error.replace("Check it in Settings → Web Search.",
                              "Make sure you copied the whole key.")
        self.status.setText(f"✓ Working. Web search will use {self.service.name}."
                            if not error else f"✗ {error}")


class WebSearchSettings(QWidget):
    """Keys save as they are entered; `keys_changed` carries the new ordered list."""

    keys_changed = Signal(list)
    _checked = Signal(str, str)

    def __init__(self, settings: QSettings, parent=None, *, check=check_search_key):
        super().__init__(parent)
        self._settings = settings
        self._check = check
        self._checked.connect(self._on_checked)

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(12)
        for text in (INTRO, PRIVACY, WHAT_IS_A_KEY, STEPS):
            label = QLabel(text, body)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText if "<" in text else Qt.TextFormat.PlainText)
            layout.addWidget(label)
        self.cards: dict[str, _ServiceCard] = {}
        for index, service in enumerate(SEARCH_SERVICES):
            card = _ServiceCard(service, settings, recommended=index == 0, parent=body)
            card.key.textEdited.connect(lambda _text, c=card: self._save(c))
            card.test.clicked.connect(lambda _checked=False, c=card: self._start_check(c))
            card.remove.clicked.connect(lambda _checked=False, c=card: self._remove(c))
            self.cards[service.id] = card
            layout.addWidget(card)
        note = QLabel(ORDER_NOTE, body)
        note.setObjectName("settingsHint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _save(self, card: _ServiceCard) -> None:
        if card.value():
            self._settings.setValue(_setting(card.service.id), card.value())
        else:
            self._settings.remove(_setting(card.service.id))
        card.show_saved()
        self.keys_changed.emit(load_search_keys(self._settings))

    def _remove(self, card: _ServiceCard) -> None:
        card.key.clear()
        self._save(card)

    def _start_check(self, card: _ServiceCard) -> None:
        card.test.setEnabled(False)
        card.test.setText("Testing…")
        card.status.setText("Checking with one search…")
        service, key = card.service.id, card.value()
        # One network request; kept off the interface thread.
        threading.Thread(target=lambda: self._checked.emit(service, self._check(service, key)),
                         daemon=True).start()

    def _on_checked(self, service: str, error: str) -> None:
        card = self.cards.get(service)
        if card is not None:
            card.show_result(error)
