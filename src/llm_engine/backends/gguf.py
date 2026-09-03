"""GGUF backend. Optional extra; ``llama_cpp`` is imported inside load."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from llm_engine.backends.protocol import ModelHandle
from llm_engine.config import default_model_dir
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

_UNAVAILABLE = "GGUF requires extra 'gguf' (llama-cpp-python)"


def _module_available(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _handle_runtime(handle: LoadedHandle) -> object:
    runtime = getattr(handle, "runtime", None)
    if runtime is None:
        raise EngineError("load_failed", "GGUF model is not loaded")
    return runtime


class GGUFBackend:
    name = BackendName.GGUF

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = Path(model_dir) if model_dir is not None else default_model_dir() / "gguf"

    def is_available(self) -> tuple[bool, str | None]:
        if not _module_available("llama_cpp"):
            return False, _UNAVAILABLE
        return True, None

    def list_models(self) -> list[LocalModel]:
        ok, reason = self.is_available()
        if not ok:
            raise EngineError("backend_unavailable", reason or _UNAVAILABLE)
        if not self._model_dir.is_dir():
            return []
        models: list[LocalModel] = []
        for path in sorted(self._model_dir.glob("*.gguf")):
            if not path.is_file():
                continue
            stat = path.stat()
            models.append(
                LocalModel(
                    ref=ModelRef(BackendName.GGUF, path.stem),
                    path=path,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime),
                    details={"file": path.name},
                )
            )
        return models

    def load(self, model: LocalModel, options: LoadOptions | None = None) -> ModelHandle:
        ok, reason = self.is_available()
        if not ok:
            raise EngineError("backend_unavailable", reason or _UNAVAILABLE)
        opts = options or LoadOptions()
        path = model.path
        if path is None or not path.is_file():
            raise EngineError("load_failed", f"GGUF model not found: {model.ref.name}")
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise EngineError("backend_unavailable", _UNAVAILABLE) from exc
        try:
            llama = Llama(model_path=str(path), n_ctx=opts.n_ctx, verbose=False)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError("load_failed", str(exc) or "GGUF load failed") from exc
        return ModelHandle(model=model, options=opts, runtime=llama)

    def unload(self, handle: LoadedHandle) -> None:
        runtime = getattr(handle, "runtime", None)
        closer = getattr(runtime, "close", None)
        if callable(closer):
            closer()

    def stream_generate(
        self,
        handle: LoadedHandle,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        llama = _handle_runtime(handle)
        if cancel.is_set():
            return
        payload = [{"role": turn.role, "content": turn.content} for turn in messages]
        try:
            stream = llama.create_chat_completion(
                messages=payload,
                temperature=params.temperature,
                top_p=params.top_p,
                max_tokens=params.max_tokens,
                stream=True,
            )
            for chunk in stream:
                if cancel.is_set():
                    return
                if not isinstance(chunk, dict):
                    continue
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield content
        except EngineError:
            raise
        except Exception as exc:
            if cancel.is_set():
                return
            raise EngineError("load_failed", str(exc) or "GGUF generate failed") from exc

    def delete(self, model: LocalModel) -> None:
        if model.path is None:
            raise EngineError("not_found", f"GGUF model not found: {model.ref.name}")
        path = model.path.resolve()
        root = self._model_dir.resolve()
        if not path.is_relative_to(root):
            raise EngineError("load_failed", "refusing to delete path outside model dir")
        if not path.is_file():
            raise EngineError("not_found", f"GGUF model not found: {model.ref.name}")
        path.unlink()
