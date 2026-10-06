from __future__ import annotations

import pytest

from llm_manager_app import updates
from test_app_shell import _qapp, _window
from test_settings_inspector import _wait_until

DMG = "Orchevian-9.9.9-macos-arm64.dmg"


def release(tag="v9.9.9", assets=None):
    return {
        "tag_name": tag,
        "html_url": "https://github.com/FincoDinco/orchevian/releases/tag/" + tag,
        "assets": assets if assets is not None else [
            {"name": DMG, "browser_download_url": "https://github.com/dl/" + DMG},
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://github.com/dl/sums"},
        ],
    }


@pytest.mark.parametrize("text,expected", [
    ("v1.2.3", (1, 2, 3)), ("1.10.0", (1, 10, 0)), ("v1.2", None), ("1.2.3rc1", None), ("", None),
])
def test_versions_are_plain_numbers(text, expected):
    assert updates.parse_version(text) == expected


def test_a_newer_release_offers_this_computers_installer():
    found = updates.newer_release("1.1.0", fetch=release, suffix="-macos-arm64.dmg")
    assert found == updates.Update(
        "9.9.9", release()["html_url"], "https://github.com/dl/" + DMG, DMG)


@pytest.mark.parametrize("current,tag", [("9.9.9", "v9.9.9"), ("10.0.0", "v9.9.9"),
                                         ("1.0.0", "nightly"), ("dev", "v9.9.9")])
def test_same_older_or_unreadable_versions_offer_nothing(current, tag):
    assert updates.newer_release(current, fetch=lambda: release(tag), suffix=".dmg") is None


def test_without_a_matching_https_installer_the_release_page_is_offered():
    insecure = [{"name": DMG, "browser_download_url": "http://github.com/dl/" + DMG}]
    for assets, suffix in ((None, "-windows-x64-setup.exe"), (insecure, "-macos-arm64.dmg")):
        found = updates.newer_release("1.0.0", fetch=lambda a=assets: release(assets=a),
                                      suffix=suffix)
        assert found.download_url == found.notes_url and found.file_name == ""


def test_installers_match_how_the_build_names_them():
    assert updates.installer_suffix("darwin", "arm64") == "-macos-arm64.dmg"
    assert updates.installer_suffix("darwin", "x86_64") is None  # No Intel build.
    assert updates.installer_suffix("win32", "AMD64") == "-windows-x64-setup.exe"
    assert updates.installer_suffix("linux", "x86_64") == "-linux-x64.AppImage"


def test_tests_never_check_automatically():
    assert not updates.automatic_checks_allowed()


def test_toolbar_offers_an_update_unless_that_version_was_skipped(tmp_path, monkeypatch):
    _qapp()
    from llm_manager_app.widgets.settings import KEY_SKIPPED_UPDATE

    window, store, _library = _window(tmp_path)
    try:
        shown = []
        monkeypatch.setattr(window, "_show_update", lambda: shown.append(True))
        update = updates.Update("9.9.9", "https://x/notes", "https://x/" + DMG, DMG)
        window._settings.setValue(KEY_SKIPPED_UPDATE, "9.9.9")
        window._on_update_checked(update, False, "")
        assert not window._update_action.isVisible() and not shown
        window._on_update_checked(update, True, "")  # Help → Check for Updates still offers it.
        assert window._update_action.isVisible() and shown == [True]
        assert window._update_button.text() == "Update to 9.9.9"
    finally:
        window.close()
        if store:
            store.close()


def test_skip_this_version_hides_the_toolbar_button(tmp_path):
    _qapp()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from llm_manager_app.widgets.settings import KEY_SKIPPED_UPDATE

    window, store, _library = _window(tmp_path)
    try:
        window._on_update_checked(
            updates.Update("9.9.9", "https://x/notes", "https://x/" + DMG, DMG), False, "")

        def skip():
            box = QApplication.activeModalWidget()
            assert isinstance(box, QMessageBox) and "9.9.9 is available" in box.text()
            next(b for b in box.buttons() if b.text() == "Skip This Version").click()

        QTimer.singleShot(0, skip)
        window._update_button.click()
        assert not window._update_action.isVisible()
        assert window._settings.value(KEY_SKIPPED_UPDATE) == "9.9.9"
    finally:
        window.close()
        if store:
            store.close()


def test_a_failed_manual_check_says_so(tmp_path, monkeypatch):
    _qapp()
    from PySide6.QtWidgets import QMessageBox

    window, store, _library = _window(tmp_path)
    try:
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
        def offline():
            raise OSError("offline")

        monkeypatch.setattr(updates, "newer_release", offline)
        window._check_for_updates(manual=True)
        _wait_until(lambda: warnings)
        assert "couldn't reach GitHub" in warnings[0] and "offline" in warnings[0]
    finally:
        window.close()
        if store:
            store.close()


def test_help_menu_has_updates_and_support_links(tmp_path):
    _qapp()
    from PySide6.QtGui import QAction

    window, store, _library = _window(tmp_path)
    try:
        for name in ("checkUpdatesAction", "websiteAction", "supportAction", "donateAction"):
            assert window.findChild(QAction, name) is not None, name
    finally:
        window.close()
        if store:
            store.close()


def test_turning_off_automatic_checks_stops_the_timer(tmp_path, monkeypatch):
    _qapp()
    from llm_manager_app.widgets.settings import KEY_CHECK_UPDATES

    monkeypatch.setattr(updates, "automatic_checks_allowed", lambda: True)
    window, store, _library = _window(tmp_path)
    try:
        window._on_check_updates_setting(True)
        assert window._update_timer.isActive()
        window._open_settings()
        window._settings_dialog._check_updates.setChecked(False)
        assert not window._update_timer.isActive()
        assert window._settings.value(KEY_CHECK_UPDATES) in (False, "false")
    finally:
        window.close()
        if store:
            store.close()


def test_about_lists_the_website_support_email_and_coffee_link():
    _qapp()
    from PySide6.QtWidgets import QLabel

    from llm_manager_app.widgets.about import AboutDialog

    dialog = AboutDialog()
    text = dialog.findChild(QLabel, "aboutSupport").text()
    for part in ("https://orchevian.com", "mailto:support@orchevian.com",
                 "https://buymeacoffee.com/sethhardin"):
        assert part in text
