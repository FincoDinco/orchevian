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
    loading: ModelRef | None = None


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
        # Guards handle/epoch. Never held during backend.load / stream_generate.
        self._meta = threading.Lock()
        self._epoch = 0
        self._generating = False
        self._handle: LoadedHandle | None = None
        self._backend: InferenceBackend | None = None
        self._conversation_id: int | None = None
        self._loading: ModelRef | None = None
        self._load_cancel = threading.Event()
        self._operation_backend: InferenceBackend | None = None

    def status(self) -> SessionStatus:
        handle = self._handle
        checker = getattr(self._backend, "is_loaded", None)
        if handle is not None and callable(checker) and not checker(handle):
            handle = None
        loaded = handle.model if handle is not None else None
        return SessionStatus(
            loaded=loaded,
            generating=self._generating or self._lock.locked(),
            conversation_id=self._conversation_id,
            loading=self._loading,
        )

    def load(
        self,
        ref: ModelRef,
        options: LoadOptions | None = None,
        on_progress: Callable[..., object] | None = None,
        *,
        cancel: threading.Event | None = None,
    ) -> LocalModel:
        opts = options if options is not None else LoadOptions()
        with self._meta:
            epoch = self._epoch
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "generation already in progress")
        with self._meta:
            self._generating = True
            self._load_cancel = cancel if cancel is not None else threading.Event()
            self._loading = ref
        try:
            if self._load_cancel.is_set():
                raise EngineError("cancelled", "Model loading stopped.")
            with self._meta:
                if self._epoch != epoch:
                    raise EngineError("cancelled", "load aborted")
                cached = (
                    self._handle.model
                    if self._handle is not None
                    and self.status().loaded is not None
                    and self._handle.model.ref == ref
                    and _handle_options(self._handle).n_ctx == opts.n_ctx
                    else None
                )
            if cached is not None:
                if on_progress is not None:
                    on_progress(1.0)
                return cached
            backend = self._registry.get(str(ref.backend))
            self._operation_backend = backend
            model = self._resolve(backend, ref)
            if self._load_cancel.is_set():
                raise EngineError("cancelled", "Model loading stopped.")
            self._drop_handle()
            if on_progress is not None:
                on_progress(0.0)
            loader = getattr(backend, "load_cancellable", None)
            handle = (
                loader(model, opts, self._load_cancel)
                if callable(loader)
                else backend.load(model, opts)
            )
            with self._meta:
                if self._epoch != epoch or self._load_cancel.is_set():
                    self._handle = None
                    self._backend = None
                    abort = True
                else:
                    self._handle = handle
                    self._backend = backend
                    abort = False
            if abort:
                backend.unload(handle)
                _log.info("unloaded %s", handle.model.ref.id)
                raise EngineError("cancelled", "load aborted")
            if on_progress is not None:
                on_progress(1.0)
            _log.info("loaded %s n_ctx=%s", ref.id, opts.n_ctx)
            return handle.model
        finally:
            if self._finish_operation(epoch, cancelled=self._load_cancel.is_set()):
                raise EngineError("cancelled", "Model loading stopped.")

    def request_stop(self) -> None:
        """Signal cancellation directly; never wait for model I/O on the caller."""
        with self._meta:
            if not self._generating:
                return
            self._epoch += 1
            self._load_cancel.set()
            backend = self._operation_backend or self._backend
            stop = getattr(backend, "request_stop", None)
            if callable(stop):
                stop()

    def _finish_operation(self, epoch: int, *, cancelled: bool = False) -> bool:
        with self._meta:
            stale = self._epoch != epoch or cancelled
            handle = self._handle if stale else None
            backend = self._backend
            if handle is not None:
                self._handle = None
                self._backend = None
            self._loading = None
            self._operation_backend = None
            self._generating = False
        try:
            if handle is not None and backend is not None:
                backend.unload(handle)
        finally:
            self._lock.release()
        return handle is not None

    def unload(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "generation already in progress")
        self._generating = True
        try:
            self._drop_handle()
        finally:
            self._generating = False
            self._lock.release()

    def delete(self, ref: ModelRef) -> None:
        """Resolve and delete under the same lock used by loading and generation."""
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "Stop the current model operation before deleting.")
        try:
            backend = self._registry.get(str(ref.backend))
            model = self._resolve(backend, ref)
            if self._handle is not None and self._handle.model.ref == ref:
                self._drop_handle()
            backend.delete(model)
        except OSError as exc:
            raise EngineError("delete_failed", f"Could not delete {ref.name}: {exc}") from exc
        finally:
            self._lock.release()

    def force_unload(self) -> bool:
        if self._generating or self._lock.locked():
            backend = self._operation_backend or self._backend
            if callable(getattr(backend, "request_stop", None)):
                self.request_stop()
                return False
        # generate/load hold `_lock` for backend I/O; waiting would hang the GUI worker.
        with self._meta:
            in_flight = self._generating or self._lock.locked()
            if in_flight:
                self._epoch += 1
            handle = self._handle
            backend = self._backend
            self._handle = None
            self._backend = None
        if handle is not None and backend is not None:
            backend.unload(handle)
            _log.info("unloaded %s", handle.model.ref.id)
            return True
        return not in_flight

    def generate(
        self,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        if not self._lock.acquire(blocking=False):
            raise EngineError("generating", "generation already in progress")
        try:
            with self._meta:
                if self._handle is None or self._backend is None:
                    raise EngineError("no_model", "no model loaded")
                self._generating = True
                epoch = self._epoch
                handle = self._handle
                backend = self._backend
        except BaseException:
            self._generating = False
            self._lock.release()
            raise

        def _stream() -> Iterator[str]:
            try:
                yield from backend.stream_generate(handle, messages, params, cancel)
            finally:
                self._finish_operation(epoch)

        return _stream()

    def _resolve(self, backend: InferenceBackend, ref: ModelRef) -> LocalModel:
        for model in backend.list_models():
            if model.ref == ref:
                return model
        raise EngineError("not_found", f"model not found: {ref.id}")

    def _drop_handle(self) -> None:
        with self._meta:
            handle = self._handle
            backend = self._backend
            self._handle = None
            self._backend = None
        if handle is None or backend is None:
            return
        backend.unload(handle)
        _log.info("unloaded %s", handle.model.ref.id)
