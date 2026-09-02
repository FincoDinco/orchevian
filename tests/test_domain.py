from datetime import datetime

import pytest

from llm_engine.domain.chat import ChatTurn, Conversation, GenerationParams, Project
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ConversationSummary,
    LoadOptions,
    LocalModel,
    ModelRef,
)


def test_backend_names_are_lowercase() -> None:
    assert BackendName.MLX == "mlx"
    assert BackendName.OLLAMA == "ollama"
    assert BackendName.GGUF == "gguf"
    assert BackendName.OLLAMA.value == "ollama"


def test_model_ref_id() -> None:
    ref = ModelRef(BackendName.OLLAMA, "qwen3:8b")
    assert ref.id == "ollama/qwen3:8b"


def test_load_options_n_ctx_default() -> None:
    assert LoadOptions().n_ctx == 8192
    assert LoadOptions(n_ctx=4096).n_ctx == 4096


def test_generation_presets() -> None:
    precise = GenerationParams.preset("precise")
    balanced = GenerationParams.preset("balanced")
    creative = GenerationParams.preset("creative")
    assert (precise.temperature, precise.top_p, precise.max_tokens) == (0.2, 0.8, 2048)
    assert (balanced.temperature, balanced.top_p, balanced.max_tokens) == (0.7, 0.9, 2048)
    assert (creative.temperature, creative.top_p, creative.max_tokens) == (1.1, 0.98, 2048)


def test_generation_preset_is_case_insensitive() -> None:
    assert GenerationParams.preset("Precise") == GenerationParams.preset("precise")
    assert GenerationParams.preset("  BALANCED\n") == GenerationParams.preset("balanced")


def test_unknown_preset_raises_key_error() -> None:
    with pytest.raises(KeyError):
        GenerationParams.preset("fast")


def test_default_generation_params_match_balanced() -> None:
    assert GenerationParams() == GenerationParams.preset("balanced")


def test_engine_error_code() -> None:
    err = EngineError("no_model")
    assert err.code == "no_model"
    assert str(err) == "no_model"
    detailed = EngineError("config_invalid", "bad port")
    assert detailed.code == "config_invalid"
    assert str(detailed) == "bad port"


def test_local_model_and_chat_types_construct() -> None:
    now = datetime(2026, 8, 28, 11, 39, 57)
    ref = ModelRef(BackendName.GGUF, "tinyllama")
    model = LocalModel(ref=ref, path=None, size_bytes=0)
    assert model.available is True
    assert model.ref.id == "gguf/tinyllama"
    turn = ChatTurn(role="user", content="hi", created_at=now)
    assert turn.tokens_per_sec is None
    project = Project(id=1, name="Work", instructions="", default_model=ref, created_at=now)
    assert project.default_model is not None
    summary = ConversationSummary(
        id=4,
        title="New Chat",
        model=ModelRef(BackendName.OLLAMA, "qwen3:8b"),
        project_id=None,
        message_count=0,
        updated_at=now,
        created_at=now,
    )
    conv = Conversation(summary=summary, system_prompt="", messages=())
    assert conv.messages == ()
    assert turn.content == "hi"
