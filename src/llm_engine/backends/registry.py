"""Backend registry. Availability is structured; unexpected errors propagate."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import replace
from functools import partial

from llm_engine import config
from llm_engine.backends.gguf import GGUFBackend
from llm_engine.backends.mlx import MLXBackend
from llm_engine.backends.ollama import OllamaBackend
from llm_engine.backends.process import ProcessBackend
from llm_engine.backends.protocol import InferenceBackend
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import LocalModel

_FAKE_ENV = "LLM_ENGINE_FAKE_BACKEND"


def default_backends(cfg: config.EngineConfig | None = None) -> list[InferenceBackend]:
    if os.environ.get(_FAKE_ENV):
        from llm_engine.backends.fake import FakeBackend

        return [FakeBackend()]
    model_dir = (cfg if cfg is not None else config.load()).model_dir
    return [
        ProcessBackend(OllamaBackend(), OllamaBackend),
        ProcessBackend(MLXBackend(model_dir / "mlx"), partial(MLXBackend, model_dir / "mlx")),
        ProcessBackend(GGUFBackend(model_dir / "gguf"), partial(GGUFBackend, model_dir / "gguf")),
    ]


class BackendRegistry:
    def __init__(
        self,
        backends: Sequence[InferenceBackend] | None = None,
        *,
        cfg: config.EngineConfig | None = None,
    ) -> None:
        self._backends = list(
            backends if backends is not None else default_backends(cfg)
        )

    def backends(self) -> tuple[InferenceBackend, ...]:
        return tuple(self._backends)

    def get(self, name: str) -> InferenceBackend:
        for backend in self._backends:
            if str(backend.name) == str(name):
                return backend
        raise EngineError("not_found", f"unknown backend: {name}")

    def list_models(self) -> tuple[list[LocalModel], dict[str, tuple[bool, str | None]]]:
        models: list[LocalModel] = []
        availability: dict[str, tuple[bool, str | None]] = {}
        for backend in self._backends:
            key = str(backend.name)
            try:
                ok, reason = backend.is_available()
                availability[key] = (ok, reason)
                if not ok and not getattr(backend, "supports_offline_catalog", False):
                    continue
                rows = backend.list_models()
                models.extend(
                    rows if ok else [
                        replace(model, available=False, unavailable_reason=reason) for model in rows
                    ]
                )
            except (EngineError, OSError) as exc:
                availability[key] = (False, str(exc))
        return models, availability

    def close(self) -> None:
        for backend in self._backends:
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()
