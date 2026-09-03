"""Inference backend protocol. Types live in ``domain.models``; this module re-exports them."""

from __future__ import annotations

from dataclasses import dataclass, field

from llm_engine.domain.models import (
    CancelToken,
    InferenceBackend,
    LoadedHandle,
    LoadOptions,
    LocalModel,
)

__all__ = [
    "CancelToken",
    "InferenceBackend",
    "LoadedHandle",
    "LoadOptions",
    "LocalModel",
    "ModelHandle",
]


@dataclass(frozen=True, slots=True)
class ModelHandle:
    """Concrete ``LoadedHandle``. ``options.n_ctx`` is Ollama ``num_ctx`` / GGUF context."""

    model: LocalModel
    options: LoadOptions = field(default_factory=LoadOptions)
    runtime: object | None = None
