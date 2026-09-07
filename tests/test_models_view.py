from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, LocalModel, ModelRef
from llm_engine.services.catalog import CatalogService
from llm_engine.services.session import ModelSession, SessionStatus


def _qapp():
    pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["llm-manager-tests"])
    return app


def _library(tmp_path: Path):
    from llm_engine.store.library import LibraryService
    from llm_engine.store.sqlite import SqliteStore

    store = SqliteStore(tmp_path / "data.db")
    return store, LibraryService(store)


def _window(tmp_path: Path, registry: BackendRegistry):
    from PySide6.QtCore import QSettings

    from llm_manager_app.main_window import MainWindow

    store, library = _library(tmp_path)
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    window = MainWindow(registry=registry, library=library, settings=settings)
    return window, store, library


def _trigger(signal, action, timeout: int = 2000) -> None:
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    seen: list[bool] = []
    signal.connect(lambda *args: seen.append(True))
    action()
    timer = QElapsedTimer()
    timer.start()
    app = QApplication.instance()
    assert app is not None
    while not seen and timer.elapsed() < timeout:
        app.processEvents()
        QTest.qWait(10)
    assert seen, "catalog worker did not finish"


def _model(
    backend: BackendName,
    name: str,
    *,
    path: Path | None = None,
    size_bytes: int = 2048,
) -> LocalModel:
    return LocalModel(ref=ModelRef(backend, name), path=path, size_bytes=size_bytes)


@dataclass
class StubCatalog:
    models: list[LocalModel]
    availability: dict[str, tuple[bool, str | None]]
    load_error: EngineError | None = None
    loaded: LocalModel | None = None
    generating: bool = False
    list_thread: threading.Thread | None = None
    load_thread: threading.Thread | None = None
    unload_thread: threading.Thread | None = None
    load_calls: list[ModelRef] = field(default_factory=list)
    unload_calls: int = 0

    def list_models(self) -> tuple[list[LocalModel], dict[str, tuple[bool, str | None]]]:
        self.list_thread = threading.current_thread()
        return list(self.models), dict(self.availability)

    def load(self, ref: ModelRef, options: object | None = None) -> LocalModel:
        del options
        self.load_thread = threading.current_thread()
        self.load_calls.append(ref)
        if self.load_error is not None:
            raise self.load_error
        for model in self.models:
            if model.ref == ref:
                self.loaded = model
                return model
        raise EngineError("not_found", ref.id)

    def unload(self) -> None:
        self.unload_thread = threading.current_thread()
        self.unload_calls += 1
        if self.load_error is not None:
            raise self.load_error
        self.loaded = None

    def status(self) -> SessionStatus:
        return SessionStatus(loaded=self.loaded, generating=self.generating)


def _view(catalog: StubCatalog, *, reveal=None):
    from llm_manager_app.widgets.models_view import ModelsView

    return ModelsView(catalog=catalog, reveal=reveal)


def test_models_view_does_not_list_on_construct() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    catalog = StubCatalog(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        assert catalog.list_thread is None
        assert view.objectName() == "modelsView"
    finally:
        view.close()


def test_model_search_filters_backends_and_clears_hidden_selection() -> None:
    app = _qapp()
    catalog = StubCatalog(
        models=[_model(BackendName.OLLAMA, "qwen"), _model(BackendName.GGUF, "tiny")],
        availability={"ollama": (True, None), "gguf": (True, None)},
    )
    view = _view(catalog)
    try:
        view.embed_detail()
        view.show()
        _trigger(view.job_finished, view.refresh)
        view._search.setText("GGUF")
        assert view.selected_model().ref.name == "tiny"
        _trigger(view.job_finished, view.refresh)
        assert view.selected_model().ref.name == "tiny"
        view._search.setText("no such model")
        app.processEvents()
        assert view.selected_model() is None
        assert view._list_empty.isVisible()
        assert not view._load_btn.isEnabled()
        assert not view._chat_btn.isEnabled()
        view._search.clear()
        assert view.selected_model().ref.name == "qwen"
        assert not view._list_empty.isVisible()
    finally:
        view.close()


def test_models_view_groups_by_backend_and_banners(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QListWidget

    gguf_path = tmp_path / "tiny.gguf"
    gguf_path.write_bytes(b"gguf")
    catalog = StubCatalog(
        models=[
            _model(BackendName.OLLAMA, "llama"),
            _model(BackendName.GGUF, "tiny", path=gguf_path),
        ],
        availability={
            "ollama": (True, None),
            "mlx": (False, "MLX requires macOS Apple Silicon and extra 'mlx'"),
            "gguf": (True, None),
        },
    )
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        labels = []
        widget = view.findChild(QListWidget, "modelsList")
        assert widget is not None
        for row in range(widget.count()):
            item = widget.item(row)
            assert item is not None
            labels.append(item.text())
        assert labels[0] == "Ollama"
        assert "llama" in labels
        assert "MLX" in labels
        assert "Unavailable" in labels
        assert "GGUF" in labels
        assert "tiny" in labels
        assert labels.index("Ollama") < labels.index("MLX") < labels.index("GGUF")
        banner = view.findChild(QLabel, "modelsBanner")
        assert banner is not None
        assert "MLX requires macOS Apple Silicon and extra 'mlx'" in banner.text()
        assert not banner.isHidden()
    finally:
        view.close()


def test_load_unload_run_off_gui_thread() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    gui_thread = threading.current_thread()
    catalog = StubCatalog(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        assert catalog.list_thread is not None
        assert catalog.list_thread is not gui_thread
        body = view.findChild(QLabel, "modelsDetailBody")
        assert body is not None
        assert "llama" in body.text()
        load_btn = view.findChild(QPushButton, "loadButton")
        assert load_btn is not None
        _trigger(view.job_finished, load_btn.click)
        assert catalog.load_calls == [ModelRef(BackendName.OLLAMA, "llama")]
        assert catalog.load_thread is not None
        assert catalog.load_thread is not gui_thread
        assert "Loaded" in view._model_state.text()
        unload_btn = view.findChild(QPushButton, "unloadButton")
        assert unload_btn is not None
        _trigger(view.job_finished, unload_btn.click)
        assert catalog.unload_calls == 1
        assert catalog.unload_thread is not None
        assert catalog.unload_thread is not gui_thread
        assert "Available on this device" in view._model_state.text()
    finally:
        view.close()


def test_generating_surfaces_engine_error() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    catalog = StubCatalog(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
        load_error=EngineError("generating", "generation already in progress"),
        generating=True,
    )
    catalog.loaded = catalog.models[0]
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        load_btn = view.findChild(QPushButton, "loadButton")
        unload_btn = view.findChild(QPushButton, "unloadButton")
        assert load_btn is not None and unload_btn is not None
        assert not load_btn.isEnabled()
        assert not unload_btn.isEnabled()
        body = view.findChild(QLabel, "modelsDetailBody")
        assert body is not None
        assert "Generating" in view._model_state.text()
    finally:
        view.close()


def test_chat_with_this_model_emits_and_reveal(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    path = tmp_path / "weights"
    path.mkdir()
    catalog = StubCatalog(
        models=[_model(BackendName.MLX, "qwen", path=path)],
        availability={"mlx": (True, None)},
    )
    seen_paths: list[Path] = []
    seen_refs: list[ModelRef] = []
    view = _view(catalog, reveal=seen_paths.append)
    view.chat_requested.connect(seen_refs.append)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        reveal_btn = view.findChild(QPushButton, "revealButton")
        chat_btn = view.findChild(QPushButton, "chatButton")
        assert reveal_btn is not None and chat_btn is not None
        assert reveal_btn.isEnabled()
        reveal_btn.click()
        assert seen_paths == [path]
        chat_btn.click()
        assert seen_refs == [ModelRef(BackendName.MLX, "qwen")]
    finally:
        view.close()


def test_reveal_disabled_without_path() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    catalog = StubCatalog(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        reveal_btn = view.findChild(QPushButton, "revealButton")
        assert reveal_btn is not None
        assert not reveal_btn.isEnabled()
    finally:
        view.close()


def test_main_window_models_section_swaps_panes(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QListWidget

    from llm_manager_app.widgets.sidebar import MODELS

    ollama = FakeBackend(
        name=BackendName.OLLAMA,
        models=[_model(BackendName.OLLAMA, "llama")],
    )
    mlx = FakeBackend(
        name=BackendName.MLX,
        available=False,
        unavailable_reason="MLX requires macOS Apple Silicon and extra 'mlx'",
    )
    window, store, library = _window(tmp_path, BackendRegistry([ollama, mlx]))
    try:
        assert window._detail_stack.currentWidget() is window._chat_view
        _trigger(window._models.job_finished, lambda: window._sidebar.select_section(MODELS))
        assert window.windowTitle() == "Models — LLM Manager"
        assert window._detail_stack.currentWidget() is window._models
        assert window._models.isAncestorOf(window._models.detail)
        banner = window.findChild(QLabel, "modelsBanner")
        assert banner is not None
        assert "MLX requires" in banner.text()
        models_list = window.findChild(QListWidget, "modelsList")
        assert models_list is not None
        texts = [models_list.item(i).text() for i in range(models_list.count())]
        assert texts[0] == "Ollama"
        assert "llama" in texts
        window._models.chat_with_selected()
        assert window._sidebar.current_section() == "chats"
        assert window._detail_stack.currentWidget() is window._chat_view
        cid = window._list.selected_id()
        assert cid is not None
        loaded = library.get_conversation(cid)
        assert loaded.summary.model == ModelRef(BackendName.OLLAMA, "llama")
        assert loaded.summary.title == "New Chat"
    finally:
        window.close()
        store.close()


def test_refresh_does_not_label_load_button_loading() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    entered = threading.Event()
    block = threading.Event()

    class Slow(StubCatalog):
        def list_models(self) -> tuple[list[LocalModel], dict[str, tuple[bool, str | None]]]:
            entered.set()
            block.wait(2)
            return super().list_models()

    catalog = Slow(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        view.show()
        view.refresh()
        assert entered.wait(2)
        load_btn = view.findChild(QPushButton, "loadButton")
        assert load_btn is not None
        assert load_btn.text() == "Load"
    finally:
        block.set()
        view.shutdown()
        view.close()


def test_load_button_shows_loading_during_load() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    entered = threading.Event()
    block = threading.Event()

    class Slow(StubCatalog):
        def load(self, ref: ModelRef, options: object | None = None) -> LocalModel:
            entered.set()
            block.wait(2)
            return super().load(ref, options)

    catalog = Slow(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        load_btn = view.findChild(QPushButton, "loadButton")
        assert load_btn is not None
        load_btn.click()
        assert entered.wait(2)
        assert load_btn.text() == "Loading…"
        finished: list[bool] = []
        view.job_finished.connect(lambda: finished.append(True))
        block.set()
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        assert app is not None
        for _ in range(50):
            if finished:
                break
            app.processEvents()
            QTest.qWait(10)
        assert finished
        assert load_btn.text() == "Load"
    finally:
        block.set()
        view.shutdown()
        view.close()


def test_successful_refresh_clears_prior_error() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    catalog = StubCatalog(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
        load_error=EngineError("generating", "generation already in progress"),
    )
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        load_btn = view.findChild(QPushButton, "loadButton")
        assert load_btn is not None
        _trigger(view.job_finished, load_btn.click)
        error = view.findChild(QLabel, "modelsError")
        assert error is not None
        assert "generating" in error.text()
        catalog.load_error = None
        _trigger(view.job_finished, view.refresh)
        assert error.text() == ""
        assert error.isHidden()
    finally:
        view.close()


def test_shutdown_joins_catalog_worker() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    entered = threading.Event()
    block = threading.Event()
    closed = False
    listed_after_close = True

    class Slow(StubCatalog):
        def list_models(self) -> tuple[list[LocalModel], dict[str, tuple[bool, str | None]]]:
            nonlocal listed_after_close
            entered.set()
            block.wait(2)
            listed_after_close = closed
            return super().list_models()

    catalog = Slow(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        view.refresh()
        assert entered.wait(2)
        done = threading.Event()

        def _shut() -> None:
            nonlocal closed
            view.shutdown()
            closed = True
            done.set()

        thread = threading.Thread(target=_shut, daemon=True)
        thread.start()
        assert not done.wait(0.2)
        block.set()
        assert done.wait(2)
        assert listed_after_close is False
        assert view.shutdown()
    finally:
        block.set()
        view.shutdown()
        view.close()


def test_main_window_close_joins_catalog_worker(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from llm_manager_app.widgets.sidebar import MODELS

    entered = threading.Event()
    block = threading.Event()

    class Slow(FakeBackend):
        def list_models(self) -> list[LocalModel]:
            entered.set()
            block.wait(2)
            return super().list_models()

    slow = Slow(models=[_model(BackendName.OLLAMA, "llama")])
    window, store, _library = _window(tmp_path, BackendRegistry([slow]))
    try:
        window._sidebar.select_section(MODELS)
        assert entered.wait(2)
        threading.Timer(0.05, block.set).start()
        window.close()
        assert window._models.shutdown()
        assert window._catalog_thread is None or not window._catalog_thread.isRunning()
    finally:
        block.set()
        window.close()
        store.close()


def test_unload_unexpected_error_is_backend_unavailable() -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QLabel, QPushButton

    class Boom(StubCatalog):
        def unload(self) -> None:
            self.unload_thread = threading.current_thread()
            self.unload_calls += 1
            raise RuntimeError("disk")

    catalog = Boom(
        models=[_model(BackendName.OLLAMA, "llama")],
        availability={"ollama": (True, None)},
    )
    view = _view(catalog)
    try:
        view.show()
        _trigger(view.job_finished, view.refresh)
        load_btn = view.findChild(QPushButton, "loadButton")
        assert load_btn is not None
        _trigger(view.job_finished, load_btn.click)
        unload_btn = view.findChild(QPushButton, "unloadButton")
        assert unload_btn is not None
        _trigger(view.job_finished, unload_btn.click)
        error = view.findChild(QLabel, "modelsError")
        assert error is not None
        assert "backend_unavailable" in error.text()
        assert "disk" in error.text()
    finally:
        view.close()


def test_main_window_load_runs_on_chat_worker(tmp_path: Path) -> None:
    try:
        _qapp()
    except Exception as exc:
        pytest.skip(f"no display: {exc}")

    from PySide6.QtWidgets import QPushButton

    from llm_manager_app.widgets.sidebar import MODELS

    gui_ident = threading.get_ident()

    class Probe(FakeBackend):
        def __init__(self) -> None:
            super().__init__(models=[_model(BackendName.OLLAMA, "llama")])
            self.load_ident: int | None = None

        def load(self, model, options=None):  # type: ignore[no-untyped-def]
            self.load_ident = threading.get_ident()
            return super().load(model, options)

    probe = Probe()
    window, store, _library = _window(tmp_path, BackendRegistry([probe]))
    try:
        _trigger(window._models.job_finished, lambda: window._sidebar.select_section(MODELS))
        load_btn = window._models.detail.findChild(QPushButton, "loadButton")
        assert load_btn is not None
        _trigger(window._models.job_finished, load_btn.click)
        assert probe.load_ident is not None
        assert probe.load_ident != gui_ident
        assert window._chat_view.banner_text() == ""
    finally:
        window.close()
        store.close()


def test_catalog_service_load_uses_session() -> None:
    fake = FakeBackend(models=[_model(BackendName.OLLAMA, "llama")])
    registry = BackendRegistry([fake])
    session = ModelSession(registry)
    catalog = CatalogService(registry, session)
    models, availability = catalog.list_models()
    assert [m.ref.id for m in models] == ["ollama/llama"]
    assert availability["ollama"] == (True, None)
    loaded = catalog.load(models[0].ref)
    assert loaded.ref.id == "ollama/llama"
    assert catalog.status().loaded is not None
    catalog.unload()
    assert catalog.status().loaded is None
