from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

from llm_engine.domain.models import BackendName, LocalModel, ModelRef
from llm_engine.services.storage import summary
from test_models_view import StubCatalog, _qapp, _trigger


def model(backend, name, size):
    return LocalModel(ModelRef(backend, name), None, size)


def test_storage_totals_unknown_backends_and_missing_folder(tmp_path, monkeypatch):
    calls = []

    def disk_usage(path):
        calls.append(path)
        return SimpleNamespace(free=12345)

    monkeypatch.setattr("llm_engine.services.storage.shutil.disk_usage", disk_usage)
    ollama = model(BackendName.OLLAMA, "one", 500)
    data = summary(
        [ollama, ollama, model(BackendName.GGUF, "split", 300)],
        {"ollama": (True, None), "gguf": (False, "no runtime")},
        model_dir=tmp_path / "missing" / "models",
    )
    rows = {row.backend: row for row in data.backends}
    assert rows["ollama"].size_bytes == 500
    assert rows["ollama"].model_count == 1
    assert rows["gguf"].size_bytes == 300
    assert not rows["mlx"].available and rows["mlx"].model_count == 0
    assert data.free_bytes == 12345
    assert calls == [tmp_path]
    assert not data.model_dir.exists()


def test_disk_failure_does_not_hide_model_totals(tmp_path, monkeypatch):
    def failed(_path):
        raise OSError("unmounted volume")

    monkeypatch.setattr("llm_engine.services.storage.shutil.disk_usage", failed)
    data = summary([model(BackendName.GGUF, "one", 1000)], {}, model_dir=tmp_path)
    assert data.free_bytes is None and "unmounted" in data.disk_error
    assert sum(row.size_bytes for row in data.backends) == 1000


def test_storage_refresh_runs_off_gui_thread_and_reveals_model_folder(tmp_path, monkeypatch):
    _qapp()
    from llm_manager_app.widgets.models_view import ModelsView

    gui_ident = threading.get_ident()
    threads = []

    def disk_usage(_path):
        threads.append(threading.get_ident())
        return SimpleNamespace(free=2048)

    monkeypatch.setattr("llm_engine.services.storage.shutil.disk_usage", disk_usage)
    monkeypatch.setattr("llm_engine.config.load", lambda: SimpleNamespace(model_dir=tmp_path))
    catalog = StubCatalog([model(BackendName.OLLAMA, "one", 1024)], {"ollama": (True, None)})
    revealed = []
    view = ModelsView(catalog=catalog, reveal=revealed.append)
    try:
        _trigger(view._storage_updated, view.refresh)
        assert threads and all(ident != gui_ident for ident in threads)
        assert "1.0 KiB" in view.storage._body.text()
        assert "2.0 KiB" in view.storage._body.text()
        assert "MLX: unavailable" in view.storage._body.text()
        view.storage._reveal_btn.click()
        assert revealed == [Path(tmp_path)]
    finally:
        view.close()
