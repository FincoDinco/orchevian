from __future__ import annotations

import os
import stat

import pytest

from llm_engine import config
from llm_engine.logging import setup_logging
from llm_engine.store.sqlite import SqliteStore

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows profiles are already private")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_new_database_and_its_journal_are_owner_only(tmp_path):
    path = tmp_path / "data.db"
    with SqliteStore(path) as store:
        with store.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS probe (x)")
            conn.execute("INSERT INTO probe VALUES (1)")
        for file in tmp_path.glob("data.db*"):
            assert _mode(file) & 0o077 == 0, (file.name, oct(_mode(file)))


def test_app_data_folder_and_existing_files_are_repaired(tmp_path, monkeypatch):
    root = tmp_path / "orchevian"
    (root / "logs").mkdir(parents=True)
    for name in ("data.db", "data.db-wal", "data.db.bak-pre-engine", "logs/engine.log"):
        (root / name).write_text("private")
        os.chmod(root / name, 0o644)
    os.chmod(root, 0o755)
    monkeypatch.setattr(config, "app_data_dir", lambda: root)
    config.secure_app_data()
    assert _mode(root) == 0o700 and _mode(root / "logs") == 0o700
    for name in ("data.db", "data.db-wal", "data.db.bak-pre-engine", "logs/engine.log"):
        assert _mode(root / name) == 0o600


def test_log_file_is_owner_only(tmp_path):
    import logging

    logger = logging.getLogger("llm_engine")
    saved = logger.handlers[:]
    logger.handlers.clear()
    try:
        path = tmp_path / "logs" / "engine.log"
        setup_logging(log_path=path)
        logger.info("probe")
        assert _mode(path) == 0o600
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = saved


def test_in_memory_database_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with SqliteStore(":memory:"):
        pass
    assert list(tmp_path.iterdir()) == []
