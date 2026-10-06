"""Shared test setup."""

import os

# Tests must never read or write the developer's real system password vault.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
# Windows created by tests must never ask GitHub for updates.
os.environ["ORCHEVIAN_UPDATE_CHECK"] = "0"
