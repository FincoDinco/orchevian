"""Single loaded-model cache. Optional extras are imported by backends inside load."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from llm_engine.backends.protocol import InferenceBackend, LoadedHandle
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    CancelToken,
    ChatTurn,
    GenerationParams,
    LoadOptions,
    LocalModel,
    ModelRef,
)
from llm_engine.logging import get_logger

_log = get_logger("session")


@dataclass(frozen=True, slots=True)
class SessionStatus:
    loaded: LocalModel | None = None
    generating: bool = False
    conversation_id: int | None = None


def _handle_options(handle: LoadedHandle) -> LoadOptions:
    options = getattr(handle, "options", None)
    if isinstance(options, LoadOptions):
        return options
    return LoadOptions()


class ModelSession:
    """One loaded handle. Same ref+n_ctx is a no-op; a different ref unloads first."""

    def __init__(self, registry: BackendRegistry) -> None:
        self._registry = registry
        self._lock = threading.Lock()
        self._generating = False
        self._handle: LoadedHandle | None = None
        self._backend: InferenceBackend | None = None
        self._conversation_id: int | None = None

    def status(self) -> SessionStatus:
        handle = self._handle
        loaded = handle.model if handle is not None else None
        return SessionStatus(
            loaded=loaded,
            generating=self._generating,
            conversation_id=self._conversation_id,
        )

    def load(
        self,
        ref: ModelRef,
        options: LoadOptions | None = None,
        on_progress: Callable[..., object] | None = None,
    ) -> LocalModel:
        opts = options if options is not None else LoadOptions()
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "generation already in progress")
        self._generating = True
        try:
            if (
                self._handle is not None
                and self._handle.model.ref == ref
                and _handle_options(self._handle).n_ctx == opts.n_ctx
            ):
                if on_progress is not None:
                    on_progress(1.0)
                return self._handle.model
            backend = self._registry.get(str(ref.backend))
            model = self._resolve(backend, ref)
            self._unload_locked()
            if on_progress is not None:
                on_progress(0.0)
            self._handle = backend.load(model, opts)
            self._backend = backend
            if on_progress is not None:
                on_progress(1.0)
            _log.info("loaded %s n_ctx=%s", ref.id, opts.n_ctx)
            return self._handle.model
        finally:
            self._generating = False
            self._lock.release()

    def unload(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "generation already in progress")
        self._generating = True
        try:
            self._unload_locked()
        finally:
            self._generating = False
            self._lock.release()

    def generate(
        self,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "generation already in progress")
        try:
            if self._handle is None or self._backend is None:
                raise EngineError("no_model", "no model loaded")
            self._generating = True
            handle = self._handle
            backend = self._backend
        except BaseException:
            self._lock.release()
            raise

        def _stream() -> Iterator[str]:
            try:
                yield from backend.stream_generate(handle, messages, params, cancel)
            finally:
                self._generating = False
                self._lock.release()

        return _stream()

    def _resolve(self, backend: InferenceBackend, ref: ModelRef) -> LocalModel:
        for model in backend.list_models():
            if model.ref == ref:
                return model
        raise EngineError("not_found", f"model not found: {ref.id}")

    def _unload_locked(self) -> None:
        handle = self._handle
        backend = self._backend
        self._handle = None
        self._backend = None
        if handle is None or backend is None:
            return
        backend.unload(handle)
        _log.info("unloaded %s", handle.model.ref.id)
