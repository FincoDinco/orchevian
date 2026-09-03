from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_manager_app.tokens import DARK, LIGHT, qss

ROOT = Path(__file__).resolve().parents[1]
APP_SRC = ROOT / "src" / "llm_manager_app"
BANNED_ROOTS = frozenset(
    {
        "sqlite3",
        "mlx_lm",
        "llama_cpp",
        "fastapi",
        "huggingface_hub",
        "pyqt_liquidglass",
    }
)


def _top_level(name: str) -> str:
    return name.split(".")[0]


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(_top_level(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(_top_level(node.module))
    return roots


def test_app_sources_do_not_import_sqlite3_or_mlx_lm() -> None:
    offenders: list[str] = []
    for path in APP_SRC.rglob("*.py"):
        for name in sorted(_imported_roots(path) & BANNED_ROOTS):
            offenders.append(f"{path.relative_to(ROOT)} imports {name}")
    assert offenders == []


def test_studio_dark_tokens_match_design() -> None:
    assert DARK.canvas == "#1C1C1E"
    assert DARK.elevated == "#2C2C2E"
    assert DARK.text == "#F5F5F7"
    assert DARK.secondary == "#8E8E93"
    assert DARK.accent == "#5B8DEF"
    assert DARK.danger == "#FF453A"
    assert DARK.radius_control == 8
    assert DARK.radius_composer == 10


def test_studio_light_tokens_match_design() -> None:
    assert LIGHT.canvas == "#F2F2F7"
    assert LIGHT.elevated == "#FFFFFF"
    assert LIGHT.text == "#1C1C1E"
    assert LIGHT.secondary == "#6C6C70"
    assert LIGHT.accent == "#3B6FDB"
    assert LIGHT.danger == "#FF3B30"


def test_studio_qss_is_small_and_uses_named_colors() -> None:
    sheet = qss(DARK)
    assert len(sheet.splitlines()) < 80
    assert DARK.canvas in sheet
    assert DARK.selection in sheet
    assert "brass" not in sheet.lower()
    assert "liquid" not in sheet.lower()
    assert "NSVisualEffectView" not in sheet


def _qapp():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["llm-manager-tests"])
    return app


def test_main_window_three_column_splitter() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QListView, QSplitter

    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.widgets.sidebar import Sidebar

    registry = BackendRegistry([FakeBackend()])
    window = MainWindow(registry=registry)
    try:
        splitter = window.findChild(QSplitter)
        assert splitter is not None
        assert splitter.count() == 3
        sidebar = splitter.widget(0)
        list_pane = splitter.widget(1)
        detail = splitter.widget(2)
        assert isinstance(sidebar, Sidebar)
        assert isinstance(list_pane, QListView)
        model = list_pane.model()
        assert model is None or model.rowCount() == 0
        assert isinstance(detail, QLabel)
        text = detail.text()
        assert "loaded: none" in text
        assert "ollama:" in text
        assert window.windowTitle() == "LLM Manager"
        assert window.minimumWidth() >= 1024
        assert window.minimumHeight() >= 680
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    finally:
        window.close()


def test_sidebar_chats_and_models() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_manager_app.main_window import MainWindow
    from llm_manager_app.widgets.sidebar import MODELS

    registry = BackendRegistry([FakeBackend()])
    window = MainWindow(registry=registry)
    try:
        sidebar = window._sidebar
        assert sidebar.current_section() == "chats"
        seen: list[str] = []
        sidebar.section_changed.connect(seen.append)
        sidebar.select_section(MODELS)
        assert sidebar.current_section() == "models"
        assert seen == ["models"]
        assert window.windowTitle() == "Models — LLM Manager"
    finally:
        window.close()


def test_importing_app_does_not_load_sqlite3() -> None:
    pytest.importorskip("PySide6")
    banned = ("mlx_lm", "llama_cpp", "fastapi", "huggingface_hub")
    before = {name for name in banned if name in sys.modules}
    # sqlite3 may already be loaded by engine tests; GUI import must not add mlx_lm etc.
    import llm_manager_app.main_window as app_main

    loaded = [name for name in banned if name in sys.modules and name not in before]
    assert loaded == []
    assert app_main.MainWindow is not None
