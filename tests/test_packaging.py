from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from llm_engine.backends import gguf, mlx
from llm_engine.logging import setup_logging

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_entry_smoke_check(tmp_path):
    report_path = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging/desktop.py"), "--smoke-test", str(report_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=90,
    )
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    assert result.returncode == 0, (report, result.stdout, result.stderr)
    assert report["ok"]
    assert "isolated document readers and original storage" in report["checks"]
    assert "shared project files and retained sources" in report["checks"]
    assert "image decoding, PDF rendering and spawned vision input" in report["checks"]
    assert "isolated artifact generators, previews and ZIP export" in report["checks"]
    assert "isolated web retrieval and retained sources (offline fixture)" in report["checks"]
    assert any(check.startswith("app icon and secret storage") for check in report["checks"])
    assert any(check.startswith("bundled runtimes") for check in report["checks"])
    assert len(report["checks"]) == 12


def test_missing_frozen_runtimes_explain_packaged_limitation(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(gguf, "_module_available", lambda name: False)
    monkeypatch.setattr(mlx, "_module_available", lambda name: False)
    monkeypatch.setattr(mlx, "_is_apple_silicon", lambda: True)
    for backend in (gguf.GGUFBackend(tmp_path), mlx.MLXBackend(tmp_path)):
        available, reason = backend.is_available()
        assert not available
        assert "desktop build" in reason
        assert "Ollama" in reason
        assert "uv sync" not in reason


def test_logging_without_desktop_console(monkeypatch, tmp_path):
    import logging

    logger = logging.getLogger("llm_engine")
    previous = logger.handlers[:]
    level, propagate = logger.level, logger.propagate
    logger.handlers = []
    try:
        monkeypatch.setattr(sys, "stderr", None)
        path = tmp_path / "engine.log"
        setup_logging(log_path=path).info("Desktop started")
        assert "Desktop started" in path.read_text()
        assert len(logger.handlers) == 1
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous
        logger.setLevel(level)
        logger.propagate = propagate


def test_version_is_the_same_everywhere():
    import tomllib

    import llm_engine
    import llm_manager_app

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert llm_engine.__version__ == llm_manager_app.__version__ == version


def test_icons_exist_for_every_platform():
    icons = ROOT / "packaging" / "icons"
    for name in ("orchevian.icns", "orchevian.ico", "orchevian-512.png"):
        assert (icons / name).stat().st_size > 1000
    assert (ROOT / "src" / "llm_manager_app" / "assets" / "orchevian.png").is_file()
    spec = (ROOT / "packaging" / "orchevian.spec").read_text()
    assert "orchevian.icns" in spec and "orchevian.ico" in spec
