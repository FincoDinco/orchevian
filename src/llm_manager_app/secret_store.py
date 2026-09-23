"""Where Orchevian keeps secrets: search-service keys and the local API key.

The operating system's password vault (macOS Keychain, Windows Credential Manager,
Linux Secret Service) is used when the `keyring` package is installed and has a
working backend. Otherwise secrets live in Orchevian's settings, whose file is
kept private to this account.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from llm_engine.config import make_private

SERVICE = "Orchevian"
API_KEY_SECRET = "api/key"


def _vault():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    # A "fail" backend or one with no priority cannot actually store anything.
    if isinstance(backend, fail.Keyring) or getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


class SecretStore:
    def __init__(self, settings: QSettings, *, vault=...) -> None:
        self._settings = settings
        self._vault = _vault() if vault is ... else vault
        self._harden()

    @property
    def in_system_vault(self) -> bool:
        return self._vault is not None

    def get(self, name: str) -> str:
        if self._vault is not None:
            try:
                value = self._vault.get_password(SERVICE, name)
            except Exception:
                value = None
            if value:
                return value
        return str(self._settings.value(f"secrets/{name}", "") or "")

    def set(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            self.delete(name)
            return
        if self._vault is not None:
            try:
                self._vault.set_password(SERVICE, name, value)
            except Exception:
                pass
            else:
                # Never leave a readable copy behind once the vault has it.
                self._settings.remove(f"secrets/{name}")
                self._settings.sync()
                return
        self._settings.setValue(f"secrets/{name}", value)
        self._settings.sync()
        self._harden()

    def delete(self, name: str) -> None:
        if self._vault is not None:
            try:
                self._vault.delete_password(SERVICE, name)
            except Exception:
                pass
        self._settings.remove(f"secrets/{name}")
        self._settings.sync()

    def adopt(self, legacy_key: str, name: str) -> None:
        """Move a secret saved as a plain setting by an earlier version."""
        legacy = str(self._settings.value(legacy_key, "") or "").strip()
        if legacy:
            if not self.get(name):
                self.set(name, legacy)
            self._settings.remove(legacy_key)
            self._settings.sync()

    def _harden(self) -> None:
        # Settings files (Linux .conf, macOS plist, test .ini) stay owner-only;
        # the Windows registry is already per-user.
        if self._settings.format() != QSettings.Format.NativeFormat or not self._is_registry():
            path = Path(self._settings.fileName())
            if path.is_file():
                make_private(path)

    def _is_registry(self) -> bool:
        return self._settings.fileName().startswith("\\HKEY")
