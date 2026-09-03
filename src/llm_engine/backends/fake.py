"""Deterministic ``InferenceBackend`` for tests. Not registered in production."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence

from llm_engine.backends.protocol import ModelHandle
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    CancelToken,
    ChatTurn,
    GenerationParams,
    LoadedHandle,
    LoadOptions,
    LocalModel,
    ModelRef,
)


def _default_models(name: BackendName) -> list[LocalModel]:
    return [LocalModel(ref=ModelRef(name, "fake"), path=None, size_bytes=0)]


class FakeBackend:
    """Yields fixed chunks; can block in load/generate or raise after N yields."""

    def __init__(
        self,
        *,
        name: BackendName = BackendName.OLLAMA,
        chunks: Sequence[str] = ("Hello", " world"),
        models: Sequence[LocalModel] | None = None,
        available: bool = True,
        unavailable_reason: str | None = None,
        block_load: threading.Event | None = None,
        block_generate: threading.Event | None = None,
        fail_after: int | None = None,
        error: BaseException | None = None,
        ignore_cancel: bool = False,
    ) -> None:
        self.name = name
        self.chunks = tuple(chunks)
        self._models = list(models) if models is not None else _default_models(name)
        self._available = available
        self._unavailable_reason = unavailable_reason
        self._block_load = block_load
        self._block_generate = block_generate
        self._fail_after = fail_after
        self._ignore_cancel = ignore_cancel
        self._error = error if error is not None else EngineError(
            "load_failed", "fake generate failed"
        )
        self.load_calls: list[tuple[LocalModel, LoadOptions | None]] = []
        self.unload_calls: list[LoadedHandle] = []
        self.delete_calls: list[LocalModel] = []

    def is_available(self) -> tuple[bool, str | None]:
        if self._available:
            return True, None
        return False, self._unavailable_reason or "fake backend unavailable"

    def list_models(self) -> list[LocalModel]:
        ok, reason = self.is_available()
        if not ok:
            raise EngineError("backend_unavailable", reason or "fake backend unavailable")
        return list(self._models)

    def load(self, model: LocalModel, options: LoadOptions | None = None) -> ModelHandle:
        self.load_calls.append((model, options))
        if self._block_load is not None:
            self._block_load.wait()
        return ModelHandle(model=model, options=options or LoadOptions())

    def unload(self, handle: LoadedHandle) -> None:
        self.unload_calls.append(handle)

    def stream_generate(
        self,
        handle: LoadedHandle,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        del handle, messages, params
        if self._fail_after is not None and self._fail_after <= 0:
            raise self._error
        yielded = 0
        for chunk in self.chunks:
            if cancel.is_set() and not self._ignore_cancel:
                return
            yield chunk
            yielded += 1
            if self._fail_after is not None and yielded >= self._fail_after:
                raise self._error
            if self._block_generate is not None and yielded == 1:
                while not self._block_generate.is_set():
                    if cancel.is_set() and not self._ignore_cancel:
                        return
                    self._block_generate.wait(timeout=0.05)

    def delete(self, model: LocalModel) -> None:
        self.delete_calls.append(model)
        self._models = [item for item in self._models if item.ref != model.ref]
