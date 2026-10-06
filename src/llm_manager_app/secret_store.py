"""Where Orchevian keeps secrets: search-service keys and the local API key.

The operating system's password vault (macOS Keychain, Windows Credential Manager,
Linux Secret Service) is used through `keyring`. Where no vault exists, such as
Linux without a keyring service, secrets live in Orchevian's settings, whose file
is kept private to this account. If a vault exists but refuses a secret, saving
fails with `SecretStoreError` rather than leaving the secret in that file.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings

from llm_engine.config import make_private

SERVICE = "Orchevian"
API_KEY_SECRET = "api/key"


class SecretStoreError(RuntimeError):
    """The system password vault exists but did not store a secret."""


def _vault_name() -> str:
    return {"darwin": "the macOS Keychain", "win32": "Windows Credential Manager"}.get(
        sys.platform, "the system password vault")


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
            except Exception as exc:
                # Not the settings file: a locked or refused vault would silently leave
                # the secret in a plain file.
                detail = f" ({exc})" if str(exc) else ""
                raise SecretStoreError(
                    f"Couldn't save the key to {_vault_name()}. Make sure it's unlocked "
                    f"and that Orchevian is allowed to use it, then try again.{detail}"
                ) from exc
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
                try:
                    self.set(name, legacy)
                except SecretStoreError:
                    return  # Keep it where it is until the vault accepts it.
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
