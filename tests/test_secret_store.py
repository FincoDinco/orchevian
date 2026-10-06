from __future__ import annotations

import os
import stat

import pytest
from PySide6.QtCore import QSettings

from llm_manager_app.secret_store import SecretStore, SecretStoreError
from llm_manager_app.widgets.web_search_settings import load_search_keys


class Vault:
    def __init__(self):
        self.items = {}

    def get_password(self, service, name):
        return self.items.get((service, name))

    def set_password(self, service, name, value):
        self.items[(service, name)] = value

    def delete_password(self, service, name):
        self.items.pop((service, name), None)


def _settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def test_system_vault_holds_secrets_and_no_readable_copy_remains(tmp_path):
    settings, vault = _settings(tmp_path), Vault()
    store = SecretStore(settings, vault=vault)
    store.set("web_search/exa", "exa-key")
    assert vault.items[("Orchevian", "web_search/exa")] == "exa-key"
    settings.sync()
    ini = tmp_path / "settings.ini"
    assert not ini.exists() or "exa-key" not in ini.read_text()
    assert store.get("web_search/exa") == "exa-key"
    store.delete("web_search/exa")
    assert store.get("web_search/exa") == ""


def test_keys_saved_by_earlier_versions_move_out_of_plain_settings(tmp_path):
    settings, vault = _settings(tmp_path), Vault()
    settings.setValue("web_search/tavily_key", "tvly-old")
    settings.sync()
    keys = load_search_keys(SecretStore(settings, vault=vault))
    assert keys == [("tavily", "tvly-old")]
    assert settings.value("web_search/tavily_key") is None
    assert vault.items[("Orchevian", "web_search/tavily")] == "tvly-old"


def test_without_a_vault_the_settings_file_is_owner_only(tmp_path):
    settings = _settings(tmp_path)
    SecretStore(settings, vault=None).set("api/key", "ov-secret")
    assert SecretStore(settings, vault=None).get("api/key") == "ov-secret"
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "settings.ini").stat().st_mode) & 0o077 == 0


class LockedVault(Vault):
    def set_password(self, service, name, value):
        raise RuntimeError("User interaction is not allowed.")


def test_vault_refusal_is_reported_and_never_falls_back_to_the_file(tmp_path):
    settings = _settings(tmp_path)
    store = SecretStore(settings, vault=LockedVault())
    with pytest.raises(SecretStoreError, match="Make sure it's unlocked"):
        store.set("web_search/exa", "exa-key")
    settings.sync()
    ini = tmp_path / "settings.ini"
    assert not ini.exists() or "exa-key" not in ini.read_text()


def test_keys_from_earlier_versions_stay_put_until_the_vault_accepts_them(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("web_search/tavily_key", "tvly-old")
    load_search_keys(SecretStore(settings, vault=LockedVault()))
    assert settings.value("web_search/tavily_key") == "tvly-old"
    vault = Vault()
    assert load_search_keys(SecretStore(settings, vault=vault)) == [("tavily", "tvly-old")]
    assert settings.value("web_search/tavily_key") is None
