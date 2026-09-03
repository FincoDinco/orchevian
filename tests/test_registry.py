from __future__ import annotations

import threading
from pathlib import Path

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.gguf import GGUFBackend
from llm_engine.backends.mlx import MLXBackend
from llm_engine.backends.ollama import OllamaBackend
from llm_engine.backends.registry import BackendRegistry, default_backends
from llm_engine.cli import main
from llm_engine.config import EngineConfig
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    GenerationParams,
    LoadOptions,
    LocalModel,
    ModelRef,
)


def _model(backend: BackendName, name: str) -> LocalModel:
    return LocalModel(ref=ModelRef(backend, name), path=None, size_bytes=4)


class _BoomBackend:
    name = BackendName.GGUF

    def is_available(self) -> tuple[bool, str | None]:
        return True, None

    def list_models(self) -> list[LocalModel]:
        raise RuntimeError("boom")


class _DownBackend:
    name = BackendName.OLLAMA

    def is_available(self) -> tuple[bool, str | None]:
        return False, "Ollama is not running at 127.0.0.1:11434"

    def list_models(self) -> list[LocalModel]:
        raise AssertionError("list_models must not be called when unavailable")


def test_default_registry_includes_ollama_mlx_gguf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ENGINE_FAKE_BACKEND", raising=False)
    backends = default_backends()
    try:
        assert [b.name for b in backends] == [
            BackendName.OLLAMA,
            BackendName.MLX,
            BackendName.GGUF,
        ]
        assert isinstance(backends[0], OllamaBackend)
        assert isinstance(backends[1], MLXBackend)
        assert isinstance(backends[2], GGUFBackend)
    finally:
        for backend in backends:
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()


def test_default_list_models_reports_mlx_gguf_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_ENGINE_FAKE_BACKEND", raising=False)
    registry = BackendRegistry()
    try:
        _models, availability = registry.list_models()
    finally:
        registry.close()
    assert "ollama" in availability
    assert "mlx" in availability
    assert "gguf" in availability
    mlx_ok, mlx_reason = availability["mlx"]
    if not mlx_ok:
        assert mlx_reason == "MLX requires macOS Apple Silicon and extra 'mlx'"


def test_env_flag_registers_fake_instead_of_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ENGINE_FAKE_BACKEND", "1")
    backends = default_backends()
    assert len(backends) == 1
    assert isinstance(backends[0], FakeBackend)


def test_default_backends_uses_config_model_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_ENGINE_FAKE_BACKEND", raising=False)
    model_dir = tmp_path / "weights"
    cfg = EngineConfig(model_dir=model_dir, api_port=8080)
    backends = default_backends(cfg)
    try:
        assert isinstance(backends[1], MLXBackend)
        assert isinstance(backends[2], GGUFBackend)
        assert backends[1]._model_dir == model_dir / "mlx"
        assert backends[2]._model_dir == model_dir / "gguf"
    finally:
        for backend in backends:
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()


def test_default_backends_does_not_swallow_config_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LLM_ENGINE_FAKE_BACKEND", raising=False)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"model_dir": "/tmp/models", "api_port": 0}\n', encoding="utf-8")
    monkeypatch.setenv("LLM_ENGINE_CONFIG", str(cfg_path))
    with pytest.raises(EngineError) as exc:
        default_backends()
    assert exc.value.code == "config_invalid"


def test_unavailable_backend_surfaces_reason_not_empty_list() -> None:
    registry = BackendRegistry([_DownBackend()])
    models, availability = registry.list_models()
    assert models == []
    assert availability["ollama"] == (False, "Ollama is not running at 127.0.0.1:11434")


def test_available_backend_models_are_listed() -> None:
    fake = FakeBackend(
        name=BackendName.OLLAMA,
        models=[_model(BackendName.OLLAMA, "qwen3:8b")],
    )
    registry = BackendRegistry([fake])
    models, availability = registry.list_models()
    assert availability["ollama"] == (True, None)
    assert [m.ref.id for m in models] == ["ollama/qwen3:8b"]


def test_empty_list_from_available_backend_is_valid() -> None:
    fake = FakeBackend(models=[])
    models, availability = BackendRegistry([fake]).list_models()
    assert models == []
    assert availability["ollama"][0] is True


def test_registry_does_not_swallow_list_errors() -> None:
    registry = BackendRegistry([_BoomBackend()])
    with pytest.raises(RuntimeError, match="boom"):
        registry.list_models()


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(EngineError) as exc:
        BackendRegistry([FakeBackend()]).get("mlx")
    assert exc.value.code == "not_found"


def test_fake_yields_deterministic_chunks() -> None:
    fake = FakeBackend(chunks=("a", "b", "c"))
    handle = fake.load(_model(BackendName.OLLAMA, "fake"))
    chunks = list(
        fake.stream_generate(
            handle, [ChatTurn("user", "hi")], GenerationParams(), threading.Event()
        )
    )
    assert chunks == ["a", "b", "c"]
    assert len(fake.load_calls) == 1
    assert fake.load_calls[0][1] is None


def test_fake_honors_cancel_between_chunks() -> None:
    fake = FakeBackend(chunks=("one", "two", "three"))
    handle = fake.load(_model(BackendName.OLLAMA, "fake"), LoadOptions())
    cancel = threading.Event()
    chunks: list[str] = []
    for chunk in fake.stream_generate(handle, [], GenerationParams(), cancel):
        chunks.append(chunk)
        cancel.set()
    assert chunks == ["one"]


def test_fake_can_block_in_load() -> None:
    gate = threading.Event()
    fake = FakeBackend(block_load=gate)
    started = threading.Event()
    done = threading.Event()
    handle_box: list[object] = []

    def worker() -> None:
        started.set()
        handle_box.append(fake.load(_model(BackendName.OLLAMA, "fake")))
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(timeout=2)
    assert not done.wait(timeout=0.1)
    gate.set()
    assert done.wait(timeout=2)
    thread.join(timeout=2)
    assert handle_box


def test_fake_can_block_mid_stream() -> None:
    gate = threading.Event()
    fake = FakeBackend(chunks=("one", "two"), block_generate=gate)
    handle = fake.load(_model(BackendName.OLLAMA, "fake"))
    got_first = threading.Event()
    finished = threading.Event()
    chunks: list[str] = []

    def worker() -> None:
        for chunk in fake.stream_generate(handle, [], GenerationParams(), threading.Event()):
            chunks.append(chunk)
            if chunk == "one":
                got_first.set()
        finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert got_first.wait(timeout=2)
    assert not finished.wait(timeout=0.1)
    assert chunks == ["one"]
    gate.set()
    assert finished.wait(timeout=2)
    thread.join(timeout=2)
    assert chunks == ["one", "two"]


def test_fake_yield_once_then_raise() -> None:
    fake = FakeBackend(chunks=("one", "two", "three"), fail_after=1)
    handle = fake.load(_model(BackendName.OLLAMA, "fake"))
    gen = fake.stream_generate(handle, [], GenerationParams(), threading.Event())
    assert next(gen) == "one"
    with pytest.raises(EngineError) as exc:
        next(gen)
    assert exc.value.code == "load_failed"


def test_fake_raise_before_first_yield() -> None:
    fake = FakeBackend(chunks=("one", "two"), fail_after=0)
    handle = fake.load(_model(BackendName.OLLAMA, "fake"))
    gen = fake.stream_generate(handle, [], GenerationParams(), threading.Event())
    with pytest.raises(EngineError) as exc:
        next(gen)
    assert exc.value.code == "load_failed"


def test_cli_models_prints_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("llm_engine.cli.setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        "llm_engine.cli.BackendRegistry",
        lambda *args, cfg=None, **kwargs: BackendRegistry(
            [
                FakeBackend(
                    available=False,
                    unavailable_reason="Ollama is not running at 127.0.0.1:11434",
                )
            ]
        ),
    )
    cfg = tmp_path / "config.json"
    db = tmp_path / "data.db"
    assert main(["models", "--config", str(cfg), "--db", str(db)]) == 0
    captured = capsys.readouterr()
    assert "unavailable" in captured.err
    assert "127.0.0.1:11434" in captured.err
    assert captured.out == ""
    assert not cfg.exists()
    assert not db.exists()


def test_cli_models_lists_fake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("llm_engine.cli.setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        "llm_engine.cli.BackendRegistry",
        lambda *args, cfg=None, **kwargs: BackendRegistry(
            [FakeBackend(models=[_model(BackendName.OLLAMA, "qwen3:8b")])]
        ),
    )
    assert main(["models", "--config", str(tmp_path / "config.json")]) == 0
    captured = capsys.readouterr()
    assert "ollama/qwen3:8b" in captured.out
    assert captured.err == ""


def test_cli_models_invalid_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("llm_engine.cli.setup_logging", lambda **_kwargs: None)
    cfg = tmp_path / "config.json"
    cfg.write_text('{"model_dir": "/tmp/models", "api_port": 0}\n', encoding="utf-8")
    assert main(["models", "--config", str(cfg)]) == 1
    captured = capsys.readouterr()
    assert "error:" in captured.err


def test_registry_close_closes_owned_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_ENGINE_FAKE_BACKEND", raising=False)
    registry = BackendRegistry()
    backend = registry.backends()[0]
    assert isinstance(backend, OllamaBackend)
    registry.close()
    with pytest.raises(RuntimeError, match="closed"):
        backend.is_available()
