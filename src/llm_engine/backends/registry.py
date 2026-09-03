"""Backend registry. Availability is structured; unexpected errors propagate."""

from __future__ import annotations

import os
from collections.abc import Sequence

from llm_engine.backends.ollama import OllamaBackend
from llm_engine.backends.protocol import InferenceBackend
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import LocalModel

_FAKE_ENV = "LLM_ENGINE_FAKE_BACKEND"


def default_backends() -> list[InferenceBackend]:
    if os.environ.get(_FAKE_ENV):
        from llm_engine.backends.fake import FakeBackend

        return [FakeBackend()]
    return [OllamaBackend()]


class BackendRegistry:
    def __init__(self, backends: Sequence[InferenceBackend] | None = None) -> None:
        self._backends = list(backends if backends is not None else default_backends())

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
            ok, reason = backend.is_available()
            availability[key] = (ok, reason)
            if not ok:
                continue
            models.extend(backend.list_models())
        return models, availability

    def close(self) -> None:
        for backend in self._backends:
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()
