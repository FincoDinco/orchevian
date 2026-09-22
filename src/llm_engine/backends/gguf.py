"""GGUF backend. Optional extra; ``llama_cpp`` is imported inside load."""

from __future__ import annotations

import importlib.util
import re
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
from llm_engine.hardware import GIB, detect_hardware

_UNAVAILABLE = (
    "GGUF runtime missing (llama-cpp-python). "
    "Run: uv sync --extra gui --extra gguf (add --extra mlx on Apple Silicon)."
)


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


def _model_files(path: Path) -> list[Path]:
    match = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})(\.gguf)", path.name, re.I)
    if match is None:
        return [path]
    if int(match[2]) != 1:
        return []
    count = int(match[3])
    return [
        path.with_name(f"{match[1]}-{index:05d}-of-{count:05d}{match[4]}")
        for index in range(1, count + 1)
    ]


class GGUFBackend:
    name = BackendName.GGUF
    supports_offline_catalog = True

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = Path(model_dir) if model_dir is not None else default_model_dir() / "gguf"

    def is_available(self) -> tuple[bool, str | None]:
        if not _module_available("llama_cpp"):
            if getattr(sys, "frozen", False):
                return False, (
                    "This desktop build does not include the GGUF runtime. "
                    "Use an Ollama model, or run Orchevian from source with the GGUF extra."
                )
            return False, _UNAVAILABLE
        return True, None

    def list_models(self) -> list[LocalModel]:
        if not self._model_dir.is_dir():
            return []
        models: list[LocalModel] = []
        for path in sorted(self._model_dir.rglob("*")):
            if path.suffix.lower() != ".gguf" or not path.is_file():
                continue
            if any(part.startswith(".") for part in path.relative_to(self._model_dir).parts):
                continue
            files = _model_files(path)
            if not files or not all(file.is_file() for file in files):
                continue
            stat = path.stat()
            models.append(
                LocalModel(
                    ref=ModelRef(
                        BackendName.GGUF,
                        path.relative_to(self._model_dir).with_suffix("").as_posix(),
                    ),
                    path=path,
                    size_bytes=sum(file.stat().st_size for file in files),
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
            import llama_cpp
        except (ImportError, OSError) as exc:
            raise EngineError("backend_unavailable", f"GGUF could not start: {exc}") from exc
        try:
            supports_gpu = getattr(llama_cpp, "llama_supports_gpu_offload", lambda: False)
            gpu_budget = detect_hardware().gpu_budget if supports_gpu() else None
            # CPU-fit recommendations must not blindly offload onto a smaller GPU.
            required = int(model.size_bytes * 1.2) + 2 * GIB
            gpu_layers = -1 if gpu_budget is not None and required <= gpu_budget else 0
            llama = llama_cpp.Llama(
                model_path=str(path), n_ctx=opts.n_ctx,
                n_gpu_layers=gpu_layers, verbose=False,
            )
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
        if any(turn.images for turn in messages):
            raise EngineError("vision_required", "This backend currently supports text only. "
                              "Use an Ollama vision model for image interpretation.")
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
        if path == root or not path.is_relative_to(root):
            raise EngineError("load_failed", "refusing to delete path outside model dir")
        if not path.is_file():
            raise EngineError("not_found", f"GGUF model not found: {model.ref.name}")
        files = _model_files(path)
        if not files or any(not file.resolve().is_relative_to(root) for file in files):
            raise EngineError("load_failed", "refusing to delete invalid model shards")
        for file in files:
            file.unlink(missing_ok=True)
        # Remove empty download folders so the same model can be installed again.
        parent = path.parent
        while parent != root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
