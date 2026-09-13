from __future__ import annotations

import ast
import sys
import threading
import types
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    GenerationParams,
    LoadOptions,
    LocalModel,
    ModelRef,
)
from llm_engine.services.session import ModelSession, SessionStatus

ROOT = Path(__file__).resolve().parents[1]


def _model(backend: BackendName, name: str) -> LocalModel:
    return LocalModel(ref=ModelRef(backend, name), path=None, size_bytes=4)


def _session(
    *models: LocalModel,
    chunks: Sequence[str] = ("Hello", " world"),
    block_generate: threading.Event | None = None,
) -> tuple[ModelSession, FakeBackend]:
    fake = FakeBackend(models=list(models), chunks=chunks, block_generate=block_generate)
    return ModelSession(BackendRegistry([fake])), fake


def _module_level_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_mlx_and_gguf_sources_do_not_import_extras_at_module_level() -> None:
    src = ROOT / "src" / "llm_engine" / "backends"
    mlx_roots = _module_level_roots(src / "mlx.py")
    assert "mlx_lm" not in mlx_roots
    assert "mlx" not in mlx_roots
    assert "llama_cpp" not in _module_level_roots(src / "gguf.py")


def test_load_invokes_on_progress() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, fake = _session(model)
    seen: list[float] = []
    loaded = session.load(model.ref, on_progress=seen.append)
    assert loaded.ref == model.ref
    assert seen == [0.0, 1.0]
    seen.clear()
    session.load(model.ref, on_progress=seen.append)
    assert seen == [1.0]
    assert len(fake.load_calls) == 1


@pytest.mark.parametrize("cached", [False, True])
def test_stop_at_load_completion_does_not_leave_a_loaded_handle(cached) -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, fake = _session(model)
    if cached:
        session.load(model.ref)

    def progress(value):
        if value == 1.0:
            session.request_stop()

    with pytest.raises(EngineError) as exc:
        session.load(model.ref, on_progress=progress)
    assert exc.value.code == "cancelled"
    assert session.status().loaded is None
    assert not session.status().generating
    assert len(fake.unload_calls) == 1
    assert session.load(model.ref).ref == model.ref


def test_load_same_ref_and_n_ctx_is_noop() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, fake = _session(model)
    first = session.load(model.ref)
    second = session.load(model.ref, LoadOptions())
    assert first.ref == model.ref
    assert second.ref == model.ref
    assert len(fake.load_calls) == 1
    assert fake.unload_calls == []
    assert session.status().loaded is not None
    assert session.status().loaded.ref == model.ref
    assert session.status().generating is False


def test_load_different_ref_unloads_first() -> None:
    a = _model(BackendName.OLLAMA, "one")
    b = _model(BackendName.OLLAMA, "two")
    session, fake = _session(a, b)
    session.load(a.ref)
    session.load(b.ref)
    assert len(fake.load_calls) == 2
    assert len(fake.unload_calls) == 1
    assert fake.unload_calls[0].model.ref == a.ref
    assert session.status().loaded is not None
    assert session.status().loaded.ref == b.ref


def test_load_different_n_ctx_unloads_first() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, fake = _session(model)
    session.load(model.ref, LoadOptions(n_ctx=8192))
    session.load(model.ref, LoadOptions(n_ctx=4096))
    assert len(fake.load_calls) == 2
    assert fake.load_calls[1][1] is not None
    assert fake.load_calls[1][1].n_ctx == 4096
    assert len(fake.unload_calls) == 1


def test_generate_without_load_raises() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, _fake = _session(model)
    with pytest.raises(EngineError) as exc:
        session.generate([ChatTurn("user", "hi")], GenerationParams(), threading.Event())
    assert exc.value.code == "no_model"
    assert session.status().loaded is None
    assert session.status().generating is False
    session.load(model.ref)
    assert session.status().loaded is not None


def test_generate_streams_from_loaded_backend() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, _fake = _session(model, chunks=("Hello", " world"))
    session.load(model.ref)
    chunks = list(
        session.generate([ChatTurn("user", "hi")], GenerationParams(), threading.Event())
    )
    assert chunks == ["Hello", " world"]
    assert session.status().generating is False


def test_unknown_ref_raises_not_found() -> None:
    session, fake = _session(_model(BackendName.OLLAMA, "fake"))
    with pytest.raises(EngineError) as exc:
        session.load(ModelRef(BackendName.OLLAMA, "missing"))
    assert exc.value.code == "not_found"
    assert fake.load_calls == []


def test_failed_switch_keeps_loaded_model() -> None:
    current = _model(BackendName.OLLAMA, "one")
    session, fake = _session(current)
    session.load(current.ref)
    with pytest.raises(EngineError) as exc:
        session.load(ModelRef(BackendName.OLLAMA, "missing"))
    assert exc.value.code == "not_found"
    assert session.status().loaded is not None
    assert session.status().loaded.ref == current.ref
    assert fake.unload_calls == []
    assert len(fake.load_calls) == 1


def test_unknown_backend_switch_keeps_loaded_model() -> None:
    current = _model(BackendName.OLLAMA, "one")
    session, fake = _session(current)
    session.load(current.ref)
    with pytest.raises(EngineError) as exc:
        session.load(ModelRef(BackendName.MLX, "qwen"))
    assert exc.value.code == "not_found"
    assert session.status().loaded is not None
    assert session.status().loaded.ref == current.ref
    assert fake.unload_calls == []


def test_force_unload_drops_handle_while_generating() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    gate = threading.Event()
    session, fake = _session(model, chunks=("one", "two"), block_generate=gate)
    session.load(model.ref)
    gen = session.generate([ChatTurn("user", "hi")], GenerationParams(), threading.Event())
    assert next(gen) == "one"
    assert session.status().generating is True
    assert session.force_unload() is True
    assert session.status().loaded is None
    assert len(fake.unload_calls) == 1
    with pytest.raises(EngineError) as exc:
        session.unload()
    assert exc.value.code == "generating"
    gate.set()
    assert list(gen) == ["two"]
    assert session.status().generating is False
    assert session.status().loaded is None


def test_force_unload_aborts_in_flight_load() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    gate = threading.Event()
    fake = FakeBackend(models=[model], block_load=gate)
    session = ModelSession(BackendRegistry([fake]))
    errors: list[BaseException] = []
    done = threading.Event()

    def worker() -> None:
        try:
            session.load(model.ref)
        except BaseException as exc:
            errors.append(exc)
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(100):
        if fake.load_calls:
            break
        done.wait(timeout=0.02)
    assert fake.load_calls
    assert session.status().generating is True
    assert session.force_unload() is False
    assert session.status().loaded is None
    gate.set()
    assert done.wait(timeout=2)
    thread.join(timeout=2)
    assert len(errors) == 1
    assert isinstance(errors[0], EngineError)
    assert errors[0].code == "cancelled"
    assert session.status().loaded is None
    assert session.status().generating is False
    assert len(fake.unload_calls) == 1


def test_unload_clears_loaded_handle() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    session, fake = _session(model)
    session.load(model.ref)
    session.unload()
    assert session.status().loaded is None
    assert len(fake.unload_calls) == 1
    session.unload()
    assert len(fake.unload_calls) == 1


def test_load_unload_generate_while_generating_raise() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    other = _model(BackendName.OLLAMA, "other")
    gate = threading.Event()
    session, _fake = _session(model, other, chunks=("one", "two"), block_generate=gate)
    session.load(model.ref)
    gen = session.generate([ChatTurn("user", "hi")], GenerationParams(), threading.Event())
    assert next(gen) == "one"
    assert session.status().generating is True
    with pytest.raises(EngineError) as exc_load:
        session.load(other.ref)
    assert exc_load.value.code == "generating"
    with pytest.raises(EngineError) as exc_unload:
        session.unload()
    assert exc_unload.value.code == "generating"
    with pytest.raises(EngineError) as exc_gen:
        session.generate([ChatTurn("user", "again")], GenerationParams(), threading.Event())
    assert exc_gen.value.code == "generating"
    gate.set()
    assert list(gen) == ["two"]
    assert session.status().generating is False


def test_status_generating_during_load() -> None:
    model = _model(BackendName.OLLAMA, "fake")
    gate = threading.Event()
    fake = FakeBackend(models=[model], block_load=gate)
    session = ModelSession(BackendRegistry([fake]))
    done = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            session.load(model.ref)
        except BaseException as exc:
            errors.append(exc)
        done.set()

    thread = threading.Thread(target=worker)
    thread.start()
    generating = False
    for _ in range(50):
        if session.status().generating:
            generating = True
            break
        if done.is_set():
            break
        done.wait(timeout=0.02)
    assert generating is True
    with pytest.raises(EngineError) as exc:
        session.load(model.ref)
    assert exc.value.code == "generating"
    gate.set()
    assert done.wait(timeout=2)
    thread.join(timeout=2)
    assert errors == []
    assert session.status().generating is False
    assert session.status().loaded is not None


def test_status_defaults() -> None:
    status = SessionStatus()
    assert status.loaded is None
    assert status.generating is False
    assert status.conversation_id is None


def test_mlx_is_available_false_when_not_apple_silicon(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_engine.backends.mlx import MLXBackend

    monkeypatch.setattr("llm_engine.backends.mlx._is_apple_silicon", lambda: False)
    monkeypatch.setattr("llm_engine.backends.mlx._module_available", lambda _name: True)
    ok, reason = MLXBackend().is_available()
    assert ok is False
    assert reason == "MLX requires macOS Apple Silicon."


def test_mlx_is_available_false_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_engine.backends.mlx import MLXBackend

    monkeypatch.setattr("llm_engine.backends.mlx._is_apple_silicon", lambda: True)
    monkeypatch.setattr("llm_engine.backends.mlx._module_available", lambda _name: False)
    ok, reason = MLXBackend().is_available()
    assert ok is False
    assert "runtime missing" in reason
    assert "--extra mlx" in reason


def test_mlx_is_available_true_on_apple_silicon_with_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_engine.backends.mlx import MLXBackend

    monkeypatch.setattr("llm_engine.backends.mlx._is_apple_silicon", lambda: True)
    monkeypatch.setattr("llm_engine.backends.mlx._module_available", lambda name: name == "mlx_lm")
    ok, reason = MLXBackend().is_available()
    assert ok is True
    assert reason is None


def test_mlx_list_models_scans_weight_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_engine.backends.mlx import MLXBackend

    monkeypatch.setattr("llm_engine.backends.mlx._is_apple_silicon", lambda: True)
    monkeypatch.setattr("llm_engine.backends.mlx._module_available", lambda _name: True)
    good = tmp_path / "qwen"
    good.mkdir()
    (good / "model.safetensors").write_bytes(b"weights")
    (good / "config.json").write_text('{"max_position_embeddings": 32768}\n', encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "not-a-dir.txt").write_text("x", encoding="utf-8")
    models = MLXBackend(tmp_path).list_models()
    assert [m.ref.id for m in models] == ["mlx/qwen"]
    assert models[0].details["context_length"] == "32768"


def test_mlx_load_imports_inside_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_engine.backends.mlx import MLXBackend
    from llm_engine.backends.protocol import ModelHandle

    monkeypatch.setattr("llm_engine.backends.mlx._is_apple_silicon", lambda: True)
    monkeypatch.setattr("llm_engine.backends.mlx._module_available", lambda _name: True)
    model_dir = tmp_path / "tiny"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"weights")
    loaded: list[str] = []

    def fake_load(path: str) -> tuple[str, SimpleNamespace]:
        loaded.append(path)
        def apply_chat_template(
            messages: object, tokenize: bool = False, add_generation_prompt: bool = True
        ) -> str:
            del messages, tokenize, add_generation_prompt
            return "PROMPT"

        tokenizer = SimpleNamespace(apply_chat_template=apply_chat_template)
        return "WEIGHTS", tokenizer

    def fake_stream(_weights: object, _tokenizer: object, prompt: str, **kwargs: object):
        assert prompt == "PROMPT"
        yield SimpleNamespace(text="hi")

    mlx_lm = types.ModuleType("mlx_lm")
    mlx_lm.load = fake_load
    mlx_lm.stream_generate = fake_stream
    monkeypatch.setitem(sys.modules, "mlx_lm", mlx_lm)
    backend = MLXBackend(tmp_path)
    model = backend.list_models()[0]
    handle = backend.load(model, LoadOptions(n_ctx=4096))
    assert isinstance(handle, ModelHandle)
    assert loaded == [str(model_dir)]
    chunks = list(
        backend.stream_generate(
            handle, [ChatTurn("user", "hi")], GenerationParams(), threading.Event()
        )
    )
    assert chunks == ["hi"]
    backend.unload(handle)


def test_mlx_unload_clears_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_engine.backends.mlx import MLXBackend
    from llm_engine.backends.protocol import ModelHandle

    cleared: list[str] = []
    mlx = types.ModuleType("mlx")
    mlx_core = types.ModuleType("mlx.core")
    mlx_core.clear_cache = lambda: cleared.append("core")
    mlx_core.metal = SimpleNamespace(clear_cache=lambda: cleared.append("metal"))
    mlx.core = mlx_core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", mlx_core)
    handle = ModelHandle(
        model=_model(BackendName.MLX, "qwen"),
        runtime=("WEIGHTS", "TOKENIZER"),
    )
    MLXBackend().unload(handle)
    assert cleared == ["core", "metal"]


def test_gguf_is_available_false_when_extra_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_engine.backends.gguf import GGUFBackend

    monkeypatch.setattr("llm_engine.backends.gguf._module_available", lambda _name: False)
    ok, reason = GGUFBackend().is_available()
    assert ok is False
    assert reason is not None
    assert "gguf" in reason


def test_gguf_load_uses_n_ctx_not_max_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_engine.backends.gguf import GGUFBackend
    from llm_engine.backends.protocol import ModelHandle

    gguf_path = tmp_path / "tinyllama.gguf"
    gguf_path.write_bytes(b"GGUF")
    captured: dict[str, object] = {}

    class _Llama:
        def __init__(self, **kwargs: object) -> None:
            captured["init"] = kwargs

        def create_chat_completion(self, **kwargs: object):
            captured["complete"] = kwargs
            return iter(
                [
                    {"choices": [{"delta": {"content": "hello"}}]},
                    {"choices": [{"delta": {"content": "!"}}]},
                ]
            )

        def close(self) -> None:
            captured["closed"] = True

    llama_cpp = types.ModuleType("llama_cpp")
    llama_cpp.Llama = _Llama
    monkeypatch.setitem(sys.modules, "llama_cpp", llama_cpp)
    monkeypatch.setattr(
        "llm_engine.backends.gguf._module_available", lambda name: name == "llama_cpp"
    )
    backend = GGUFBackend(tmp_path)
    model = backend.list_models()[0]
    assert model.ref.id == "gguf/tinyllama"
    handle = backend.load(model, LoadOptions(n_ctx=8192))
    assert isinstance(handle, ModelHandle)
    assert captured["init"]["n_ctx"] == 8192
    assert captured["init"]["model_path"] == str(gguf_path)
    assert "max_tokens" not in captured["init"]
    chunks = list(
        backend.stream_generate(
            handle,
            [ChatTurn("user", "hi")],
            GenerationParams(max_tokens=2048),
            threading.Event(),
        )
    )
    assert chunks == ["hello", "!"]
    assert captured["complete"]["max_tokens"] == 2048
    backend.unload(handle)
    assert captured.get("closed") is True


def test_gguf_load_default_n_ctx_is_8192(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_engine.backends.gguf import GGUFBackend

    (tmp_path / "model.gguf").write_bytes(b"GGUF")
    captured: dict[str, object] = {}

    class _Llama:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    llama_cpp = types.ModuleType("llama_cpp")
    llama_cpp.Llama = _Llama
    monkeypatch.setitem(sys.modules, "llama_cpp", llama_cpp)
    monkeypatch.setattr("llm_engine.backends.gguf._module_available", lambda _name: True)
    backend = GGUFBackend(tmp_path)
    backend.load(backend.list_models()[0])
    assert captured["n_ctx"] == 8192
    assert captured.get("max_tokens") is None
