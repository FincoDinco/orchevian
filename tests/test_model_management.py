import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.gguf import GGUFBackend
from llm_engine.backends.mlx import MLXBackend, _chat_prompt
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ChatTurn, GenerationParams, LocalModel, ModelRef
from llm_engine.hardware import GIB, GPU, Hardware
from llm_engine.services.session import ModelSession


def test_delete_unloads_selected_model_and_preserves_other_models():
    model = LocalModel(ModelRef(BackendName.OLLAMA, "one"), None, 1)
    other = replace(model, ref=ModelRef(BackendName.OLLAMA, "two"))
    backend = FakeBackend(models=[model, other])
    session = ModelSession(BackendRegistry([backend]))
    session.load(model.ref)
    session.delete(model.ref)
    assert session.status().loaded is None
    assert len(backend.unload_calls) == 1
    assert backend.delete_calls == [model]
    assert backend.list_models() == [other]
    with pytest.raises(EngineError, match="not found"):
        session.delete(model.ref)


def test_delete_rejected_while_generating():
    backend = FakeBackend()
    session = ModelSession(BackendRegistry([backend]))
    model = backend.list_models()[0]
    session.load(model.ref)
    stream = session.generate([], GenerationParams(), threading.Event())
    try:
        with pytest.raises(EngineError) as exc:
            session.delete(model.ref)
        assert exc.value.code == "generating"
        assert backend.delete_calls == []
    finally:
        list(stream)


@pytest.mark.parametrize("kind", ["mlx", "gguf"])
def test_local_models_visible_and_deletable_without_runtime(tmp_path, monkeypatch, kind):
    root = tmp_path / kind
    folder = root / "owner" / "tiny"
    folder.mkdir(parents=True)
    path = folder / ("model.safetensors" if kind == "mlx" else "tiny.gguf")
    path.write_bytes(b"test")
    backend = MLXBackend(root) if kind == "mlx" else GGUFBackend(root)
    monkeypatch.setattr(backend, "is_available", lambda: (False, "Runtime missing"))
    registry = BackendRegistry([backend])
    models, availability = registry.list_models()
    assert len(models) == 1 and not models[0].available
    assert not availability[kind][0]
    assert models[0].ref.name.startswith("owner/")
    ModelSession(registry).delete(models[0].ref)
    assert not path.exists() and root.exists()


@pytest.mark.parametrize("kind", ["mlx", "gguf"])
def test_delete_refuses_model_root_and_symlinks_outside_root(tmp_path, kind):
    root = tmp_path / "models"
    root.mkdir()
    outside = tmp_path / "keep"
    outside.mkdir()
    link = root / "link"
    link.symlink_to(outside, target_is_directory=True)
    backend = MLXBackend(root) if kind == "mlx" else GGUFBackend(root)
    for path in (root, link):
        model = LocalModel(ModelRef(BackendName(kind), "bad"), path, 0)
        with pytest.raises(EngineError, match="refusing"):
            backend.delete(model)
    assert outside.exists() and root.exists()


def test_one_backend_failure_does_not_hide_other_models():
    good = FakeBackend()

    class Broken(GGUFBackend):
        def is_available(self):
            return True, None

        def list_models(self):
            raise EngineError("backend_unavailable", "GGUF failed")

    models, availability = BackendRegistry([Broken(), good]).list_models()
    assert models == good.list_models()
    assert availability["gguf"] == (False, "GGUF failed")


def test_mlx_tokenizer_without_chat_template_uses_plain_prompt():
    class Tokenizer:
        chat_template = None

        def apply_chat_template(self, *args, **kwargs):
            raise AssertionError("No chat template")

    assert _chat_prompt(Tokenizer(), [ChatTurn("user", "Hello")]) == "user: Hello\nassistant: "


@pytest.mark.parametrize("vram,expected", [(4, 0), (24, -1)])
def test_gguf_only_offloads_when_model_fits_gpu_memory(tmp_path, monkeypatch, vram, expected):
    path = tmp_path / "model.gguf"
    path.write_bytes(b"GGUF")
    model = LocalModel(ModelRef(BackendName.GGUF, "model"), path, 8 * GIB)
    hardware = Hardware("Linux", "x86_64", 64 * GIB, gpus=(GPU("GPU", "NVIDIA", vram * GIB),))
    captured = {}

    def llama(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setitem(sys.modules, "llama_cpp", SimpleNamespace(
        Llama=llama, llama_supports_gpu_offload=lambda: True,
    ))
    monkeypatch.setattr("llm_engine.backends.gguf.detect_hardware", lambda: hardware)
    GGUFBackend(tmp_path).load(model)
    assert captured["n_gpu_layers"] == expected
