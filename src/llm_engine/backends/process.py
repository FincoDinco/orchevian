"""Keep model runtimes in disposable spawned processes, outside the GUI's GIL.

Each runtime owns a private pipe. Cancellation discards both the process and pipe;
no database connections or shared queues are passed to a child.
"""

from __future__ import annotations

import multiprocessing
import threading
import time
from collections.abc import Callable, Iterator
from multiprocessing.connection import Connection

from llm_engine.backends.protocol import ModelHandle
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    CancelToken,
    ChatTurn,
    GenerationParams,
    InferenceBackend,
    LoadedHandle,
    LoadOptions,
    LocalModel,
)


def _serve(connection: Connection, factory: Callable[[], InferenceBackend]) -> None:
    backend = factory()
    handle = None
    try:
        while True:
            command, args = connection.recv()
            try:
                if command == "load":
                    handle = backend.load(*args)
                    connection.send(("loaded", handle.model))
                elif command == "generate":
                    if handle is None:
                        raise EngineError("no_model", "No model loaded.")
                    for chunk in backend.stream_generate(handle, *args, threading.Event()):
                        # Keep responses small so cancellation cannot strand a partial large read.
                        for offset in range(0, len(chunk), 4096):
                            connection.send(("token", chunk[offset : offset + 4096]))
                    connection.send(("done", None))
                elif command == "unload":
                    if handle is not None:
                        backend.unload(handle)
                    connection.send(("done", None))
                    return
            except Exception as exc:
                code = exc.code if isinstance(exc, EngineError) else "load_failed"
                connection.send(("error", (code, str(exc)[:8000])))
                return
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()
        close = getattr(backend, "close", None)
        if callable(close):
            close()


class ProcessBackend:
    """Catalog calls stay lightweight; load and inference use a killable worker."""

    def __init__(
        self,
        catalog: InferenceBackend,
        factory: Callable[[], InferenceBackend],
        *,
        load_timeout: float = 120.0,
        first_token_timeout: float = 120.0,
        token_timeout: float = 60.0,
    ) -> None:
        self.name = catalog.name
        self.catalog = catalog
        self._factory = factory
        self._load_timeout = load_timeout
        self._first_token_timeout = first_token_timeout
        self._token_timeout = token_timeout
        self._process = None
        self._connection: Connection | None = None
        self._stop = threading.Event()
        self._model: LocalModel | None = None

    def is_available(self) -> tuple[bool, str | None]:
        return self.catalog.is_available()

    def list_models(self) -> list[LocalModel]:
        return self.catalog.list_models()

    @property
    def supports_offline_catalog(self) -> bool:
        return bool(getattr(self.catalog, "supports_offline_catalog", False))

    def delete(self, model: LocalModel) -> None:
        self.catalog.delete(model)

    def request_stop(self) -> None:
        # Safe from the GUI thread, even while the child is blocked in native code.
        self._stop.set()

    def is_loaded(self, handle: LoadedHandle) -> bool:
        process = self._process
        try:
            return (
                process is not None
                and process.is_alive()
                and not self._stop.is_set()
                and getattr(handle, "runtime", None) is process
            )
        except ValueError:  # The owner finished closing the process during a GUI status read.
            return False

    def load(self, model: LocalModel, options: LoadOptions | None = None) -> ModelHandle:
        return self.load_cancellable(model, options, threading.Event())

    def load_cancellable(
        self, model: LocalModel, options: LoadOptions | None, cancel: CancelToken
    ) -> ModelHandle:
        if cancel.is_set():
            raise EngineError("cancelled", "Model loading stopped.")
        if self._process is not None:
            self._dispose()
            self._release_remote()
        self._stop.clear()
        self._model = model
        context = multiprocessing.get_context("spawn")
        connection, child = context.Pipe()
        process = context.Process(
            target=_serve, args=(child, self._factory), name=f"model-{self.name}", daemon=True
        )
        self._connection, self._process = connection, process
        try:
            process.start()
            child.close()
            connection.send(("load", (model, options)))
            kind, loaded_model = self._receive(cancel, time.monotonic() + self._load_timeout)
            if kind != "loaded":
                raise EngineError("load_failed", "Model worker returned an invalid response.")
            return ModelHandle(loaded_model, options or LoadOptions(), runtime=process)
        except BaseException as exc:
            child.close()
            self._dispose()
            try:
                self._release_remote()
            except EngineError as cleanup:
                if isinstance(exc, EngineError) and exc.code != "cancelled":
                    raise EngineError(exc.code, f"{exc}\n{cleanup}") from exc
                raise
            raise

    def _receive(self, cancel: CancelToken, deadline: float | None) -> tuple[str, object]:
        connection = self._connection
        while True:
            if cancel.is_set() or self._stop.is_set():
                raise EngineError("cancelled", "Model stopped.")
            if deadline is not None and time.monotonic() >= deadline:
                raise EngineError(
                    "load_timeout", "Model took too long to respond and was stopped. "
                    "Try a smaller model or free some memory before trying again."
                )
            try:
                if connection is not None and connection.poll(0.05):
                    kind, payload = connection.recv()
                    if kind == "error":
                        raise EngineError(*payload)
                    return kind, payload
            except (EOFError, OSError) as exc:
                raise EngineError("load_failed", "Model worker exited unexpectedly.") from exc
            if self._process is None or not self._process.is_alive():
                raise EngineError("load_failed", "Model worker exited unexpectedly.")

    def stream_generate(
        self,
        handle: LoadedHandle,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        if not self.is_loaded(handle):
            raise EngineError("no_model", "Model was stopped. Load it again to continue.")
        completed = False
        try:
            self._connection.send(("generate", (messages, params)))
            deadline = time.monotonic() + self._first_token_timeout
            while True:
                kind, payload = self._receive(cancel, deadline)
                if kind == "done":
                    completed = True
                    return
                if kind != "token":
                    raise EngineError("load_failed", "Model worker returned an invalid response.")
                deadline = time.monotonic() + self._token_timeout
                yield payload
        except EngineError as exc:
            if exc.code != "cancelled":
                raise
        finally:
            if not completed or cancel.is_set() or self._stop.is_set():
                self._dispose()
                self._release_remote()

    def unload(self, handle: LoadedHandle) -> None:
        released = False
        try:
            if self.is_loaded(handle):
                self._connection.send(("unload", ()))
                self._receive(threading.Event(), time.monotonic() + 5.0)
                released = True
        finally:
            self._dispose()
            if released:
                self._model = None
            else:
                self._release_remote()

    def _dispose(self) -> None:
        process = self._process
        connection = self._connection
        if process is not None and process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(0.5)
            if process.is_alive():
                process.kill()
                process.join(0.5)
            if process.is_alive():
                raise EngineError("stop_failed", "The operating system could not stop the model.")
            self._process = None
            process.close()
        else:
            self._process = None
        self._connection = None
        if connection is not None:
            connection.close()

    def _release_remote(self) -> None:
        # Ollama is an external service: closing our socket stops the request, while
        # keep_alive=0 asks the service to release the model's memory as well.
        release = getattr(self.catalog, "release_model", None)
        model, self._model = self._model, None
        if model is not None and callable(release):
            release(model)

    def close(self) -> None:
        self._dispose()
        self._release_remote()
        close = getattr(self.catalog, "close", None)
        if callable(close):
            close()
