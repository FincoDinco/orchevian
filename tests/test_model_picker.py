from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    Conversation,
    ConversationSummary,
    LocalModel,
    ModelRef,
)
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore

OLLAMA_DOWN = "Ollama is not running at 127.0.0.1:11434"
REF = ModelRef(BackendName.OLLAMA, "fake")
LOCAL = LocalModel(ref=REF, path=None, size_bytes=0)
QWEN = LocalModel(ref=ModelRef(BackendName.OLLAMA, "qwen3:8b"), path=None, size_bytes=1)
MLX = LocalModel(ref=ModelRef(BackendName.MLX, "qwen2-7b"), path=None, size_bytes=2)


def _qapp():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["orchevian-tests"])
    return app


def _library(tmp_path: Path) -> tuple[SqliteStore, LibraryService]:
    store = SqliteStore(tmp_path / "data.db")
    return store, LibraryService(store)


def _wait_until(predicate, timeout: float = 5.0, message: str = "timeout") -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


def _conv(cid: int = 1, model: ModelRef | None = None) -> Conversation:
    now = datetime.now()
    return Conversation(
        summary=ConversationSummary(
            id=cid,
            title="New Chat",
            model=model,
            project_id=None,
            message_count=0,
            updated_at=now,
            created_at=now,
        ),
        system_prompt="",
        messages=(),
    )


def _window(tmp_path: Path, fake: FakeBackend | list[FakeBackend] | None = None):
    from llm_manager_app.main_window import MainWindow

    store, library = _library(tmp_path)
    backends: list[FakeBackend]
    if fake is None:
        backends = [FakeBackend(models=[LOCAL])]
    elif isinstance(fake, list):
        backends = fake
    else:
        backends = [fake]
    registry = BackendRegistry(backends)
    window = MainWindow(registry=registry, library=library)
    return window, store, library, backends


def test_picker_grouped_by_backend_and_set_model(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton, QToolButton

    ollama = FakeBackend(name=BackendName.OLLAMA, models=[LOCAL, QWEN])
    mlx = FakeBackend(name=BackendName.MLX, models=[MLX])
    window, store, library, _backends = _window(tmp_path, [ollama, mlx])
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        picker = window.findChild(QToolButton, "modelPicker")
        assert picker is not None
        _wait_until(lambda: picker.has_models(), message="timed out waiting for catalog")
        send = window.findChild(QPushButton, "sendButton")
        assert send is not None and not send.isEnabled()

        menu = picker.menu()
        assert menu is not None
        texts = [action.text() for action in menu.actions()]
        assert "Ollama" in texts
        assert "MLX" in texts
        assert "Fake" in texts
        assert "Qwen 3 · 8B" in texts
        assert "Qwen 2 · 7B" in texts
        assert "Manage Models…" in texts
        ollama_at = texts.index("Ollama")
        mlx_at = texts.index("MLX")
        assert ollama_at < texts.index("Fake") < mlx_at < texts.index("Qwen 2 · 7B")

        for action in menu.actions():
            if action.text() == "Qwen 3 · 8B":
                action.trigger()
                break
        else:
            raise AssertionError("qwen3:8b action missing")
        app.processEvents()
        loaded = library.get_conversation(cid)
        assert loaded.summary.model == QWEN.ref
        assert picker.text() == "Qwen 3 · 8B"
        assert send.isEnabled()
    finally:
        window.close()
        store.close()


def test_manage_models_switches_section(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_manager_app.widgets.sidebar import MODELS

    window, store, library, _backends = _window(tmp_path)
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        picker = window._chat_view.picker()
        _wait_until(lambda: picker.has_models(), message="timed out waiting for catalog")
        menu = picker.menu()
        assert menu is not None
        for action in menu.actions():
            if action.text() == "Manage Models…":
                action.trigger()
                break
        else:
            raise AssertionError("Manage Models… missing")
        app.processEvents()
        assert window._sidebar.current_section() == MODELS
        assert window.windowTitle() == "Models — Orchevian"
    finally:
        window.close()
        store.close()


def test_ollama_down_empty_state_and_open_models(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    from llm_manager_app.widgets.sidebar import MODELS

    fake = FakeBackend(available=False, unavailable_reason=OLLAMA_DOWN)
    window, store, library, _backends = _window(tmp_path, fake)
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        _wait_until(
            lambda: OLLAMA_DOWN in (window.findChild(QLabel, "modelEmpty").text() or ""),
            message="timed out waiting for Ollama-down copy",
        )
        empty = window.findChild(QLabel, "modelEmpty")
        assert empty is not None
        assert "No models available." in empty.text()
        open_btn = window.findChild(QPushButton, "openModelsButton")
        assert open_btn is not None and open_btn.isVisible()
        send = window.findChild(QPushButton, "sendButton")
        assert send is not None and not send.isEnabled()
        picker = window._chat_view.picker()
        menu = picker.menu()
        assert menu is not None
        texts = [action.text() for action in menu.actions()]
        assert OLLAMA_DOWN in texts
        open_btn.click()
        app.processEvents()
        assert window._sidebar.current_section() == MODELS
    finally:
        window.close()
        store.close()


def test_list_models_not_on_gui_thread(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    gui_ident = threading.get_ident()

    class Probe(FakeBackend):
        def __init__(self) -> None:
            super().__init__(models=[LOCAL])
            self.list_ident: int | None = None

        def list_models(self):  # type: ignore[no-untyped-def]
            self.list_ident = threading.get_ident()
            return super().list_models()

    probe = Probe()
    window, store, library, _backends = _window(tmp_path, probe)
    try:
        cid = library.create_conversation(model=REF).summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        _wait_until(
            lambda: probe.list_ident is not None,
            message="timed out waiting for list_models",
        )
        assert probe.list_ident != gui_ident
    finally:
        window.close()
        store.close()


def test_catalog_failure_empty_state(tmp_path: Path) -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    class Boom(FakeBackend):
        def list_models(self):  # type: ignore[no-untyped-def]
            raise EngineError("backend_unavailable", "catalog exploded")

    window, store, library, _backends = _window(tmp_path, Boom())
    try:
        cid = library.create_conversation().summary.id
        window._list.refresh(select_id=cid)
        window.show()
        app.processEvents()
        _wait_until(
            lambda: "catalog exploded" in (window.findChild(QLabel, "modelEmpty").text() or ""),
            message="timed out waiting for catalog error",
        )
        empty = window.findChild(QLabel, "modelEmpty")
        assert empty is not None
        assert "No models available." in empty.text()
        send = window.findChild(QPushButton, "sendButton")
        assert send is not None and not send.isEnabled()
        open_btn = window.findChild(QPushButton, "openModelsButton")
        assert open_btn is not None and open_btn.isVisible()
        assert window._chat_view.picker().isEnabled()
    finally:
        window.close()
        store.close()


def test_catalog_error_clears_after_successful_list() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel

    from llm_manager_app.widgets.chat_view import ChatView

    view = ChatView()
    try:
        view.show()
        app.processEvents()
        view.set_conversation(_conv())
        view.on_catalog_failed("backend_unavailable", "catalog exploded")
        empty = view.findChild(QLabel, "modelEmpty")
        assert empty is not None
        assert "catalog exploded" in empty.text()
        view.set_catalog([LOCAL], {"ollama": (True, None)})
        assert "catalog exploded" not in empty.text()
        assert "Select a model" in empty.text()
    finally:
        view.close()


def test_failed_refresh_keeps_loaded_catalog() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    from llm_manager_app.widgets.chat_view import ChatView

    view = ChatView()
    try:
        view.show()
        app.processEvents()
        view.set_conversation(_conv())
        view.set_catalog([LOCAL], {"ollama": (True, None)})
        picker = view.picker()
        empty = view.findChild(QLabel, "modelEmpty")
        assert picker.has_models()
        assert empty is not None
        assert "Select a model" in empty.text()
        view.on_catalog_failed("backend_unavailable", "catalog exploded")
        assert picker.has_models()
        assert "catalog exploded" not in empty.text()
        assert "No models available." not in empty.text()
        assert "Select a model" in empty.text()

        view.set_conversation(_conv(model=REF))
        send = view.findChild(QPushButton, "sendButton")
        assert send is not None and send.isEnabled()
        view.on_catalog_failed("backend_unavailable", "catalog exploded")
        assert picker.has_models()
        assert send.isEnabled()
    finally:
        view.close()


def test_picker_disabled_until_catalog_ready() -> None:
    try:
        app = _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    from llm_manager_app.widgets.chat_view import ChatView

    view = ChatView()
    try:
        view.show()
        app.processEvents()
        view.set_conversation(_conv())
        picker = view.picker()
        send = view.findChild(QPushButton, "sendButton")
        assert not picker.isEnabled()
        assert send is not None and not send.isEnabled()
        menu = picker.menu()
        assert menu is not None
        assert "Fake" not in [action.text() for action in menu.actions()]
        view.set_catalog([LOCAL], {"ollama": (True, None)})
        assert picker.isEnabled()
        assert picker.has_models()
        assert "Fake" in [action.text() for action in menu.actions()]
    finally:
        view.close()
