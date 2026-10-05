from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from llm_manager_app.widgets.web_search_settings import (  # noqa: E402
    WebSearchSettings,
    load_search_keys,
)


def _app():
    return QApplication.instance() or QApplication([])


def _settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def _wait(app, predicate, timeout=2.0):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_keys_save_as_pasted_in_service_order_and_can_be_removed(tmp_path):
    _app()
    settings = _settings(tmp_path)
    page = WebSearchSettings(settings, check=lambda service, key: "")
    changes = []
    page.keys_changed.connect(changes.append)
    assert page.cards["tavily"].status.text() == "Not set up."
    assert not page.cards["tavily"].test.isEnabled()

    for service, key in (("serper", "s-key"), ("tavily", " tvly-key ")):
        page.cards[service].key.setText(key)
        page.cards[service].key.textEdited.emit(key)
    assert load_search_keys(settings) == [("serper", "s-key"), ("tavily", "tvly-key")]
    assert changes[-1] == [("serper", "s-key"), ("tavily", "tvly-key")]
    assert "Click Test" in page.cards["tavily"].status.text()

    page.cards["tavily"].remove.click()
    assert load_search_keys(settings) == [("serper", "s-key")]
    assert page.cards["tavily"].key.text() == ""
    # A fresh page shows what was saved.
    assert WebSearchSettings(settings).cards["serper"].key.text() == "s-key"


def test_test_button_reports_success_or_the_reason_in_plain_words(tmp_path):
    app = _app()
    results = {"good": "", "bad": "Tavily did not accept your API key."}
    page = WebSearchSettings(_settings(tmp_path), check=lambda service, key: results[key])
    card = page.cards["tavily"]
    for key, expected in (("good", "✓ Working"), ("bad", "✗ Tavily did not accept")):
        card.key.setText(key)
        card.key.textEdited.emit(key)
        card.test.click()
        _wait(app, lambda e=expected: card.status.text().startswith(e))
        assert card.test.text() == "Test"


def test_every_service_explains_its_free_plan_and_links_to_signup(tmp_path):
    _app()
    page = WebSearchSettings(_settings(tmp_path))
    text = " ".join(label.text() for label in page.findChildren(type(page.cards["tavily"].status)))
    assert "What is an API key?" in text and "How to add one" in text
    assert "No credit card" in text and "Needs a credit card" in text
    assert page.cards["exa"].findChild(type(page.cards["exa"].status),
                                       "searchServiceBadge") is not None
    assert "Exa's free search" in text


def test_a_key_the_vault_refuses_shows_why_and_is_not_used(tmp_path):
    from llm_manager_app.secret_store import SecretStore
    from test_secret_store import LockedVault

    _app()
    page = WebSearchSettings(SecretStore(_settings(tmp_path), vault=LockedVault()))
    changes = []
    page.keys_changed.connect(changes.append)
    page.cards["exa"].key.setText("exa-key")
    page.cards["exa"].key.textEdited.emit("exa-key")
    assert page.cards["exa"].status.text().startswith("✗ Couldn't save the key")
    assert changes == []
