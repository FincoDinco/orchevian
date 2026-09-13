"""MLX backend. Darwin/arm64 extra only; ``mlx_lm`` is imported inside load/generate."""

from __future__ import annotations

import importlib.util
import json
import platform
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

_UNAVAILABLE = "MLX requires macOS Apple Silicon."
_INSTALL = "MLX runtime missing. Run: uv sync --extra gui --extra mlx --extra gguf"


def _is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}


def _module_available(name: str) -> bool:
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _handle_runtime(handle: LoadedHandle) -> tuple[object, object]:
    runtime = getattr(handle, "runtime", None)
    if not isinstance(runtime, tuple) or len(runtime) != 2:
        raise EngineError("load_failed", "MLX model is not loaded")
    return runtime[0], runtime[1]


def _dir_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _mlx_details(path: Path) -> dict[str, str]:
    config_file = path / "config.json"
    if not config_file.is_file():
        return {}
    try:
        raw = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    details: dict[str, str] = {}
    mapping = (
        ("max_position_embeddings", "context_length"),
        ("hidden_size", "hidden_size"),
        ("num_hidden_layers", "num_layers"),
    )
    for src, dest in mapping:
        value = raw.get(src)
        if value is not None:
            details[dest] = str(value)
    return details


def _chat_prompt(tokenizer: object, messages: list[ChatTurn]) -> str:
    payload = [{"role": turn.role, "content": turn.content} for turn in messages]
    apply = getattr(tokenizer, "apply_chat_template", None)
    has_template = getattr(
        tokenizer, "has_chat_template", getattr(tokenizer, "chat_template", True)
    )
    if callable(apply) and has_template:
        return str(apply(payload, tokenize=False, add_generation_prompt=True))
    return "".join(f"{turn.role}: {turn.content}\n" for turn in messages) + "assistant: "


def _delta_text(response: object) -> str:
    if isinstance(response, str):
        return response
    text = getattr(response, "text", None)
    return text if isinstance(text, str) else ""


class MLXBackend:
    name = BackendName.MLX
    supports_offline_catalog = True

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = Path(model_dir) if model_dir is not None else default_model_dir() / "mlx"

    def is_available(self) -> tuple[bool, str | None]:
        if not _is_apple_silicon():
            return False, _UNAVAILABLE
        if not _module_available("mlx_lm"):
            return False, _INSTALL
        return True, None

    def list_models(self) -> list[LocalModel]:
        if not self._model_dir.is_dir():
            return []
        models: list[LocalModel] = []
        paths = {
            weight.parent for pattern in ("*.safetensors", "*.npz")
            for weight in self._model_dir.rglob(pattern)
        }
        for path in sorted(paths):
            if any(part.startswith(".") for part in path.relative_to(self._model_dir).parts):
                continue
            stat = path.stat()
            models.append(
                LocalModel(
                    ref=ModelRef(BackendName.MLX, path.relative_to(self._model_dir).as_posix()),
                    path=path,
                    size_bytes=_dir_size(path),
                    modified_at=datetime.fromtimestamp(stat.st_mtime),
                    details=_mlx_details(path),
                )
            )
        return models

    def load(self, model: LocalModel, options: LoadOptions | None = None) -> ModelHandle:
        ok, reason = self.is_available()
        if not ok:
            raise EngineError("backend_unavailable", reason or _UNAVAILABLE)
        path = model.path
        if path is None or not path.exists():
            raise EngineError("load_failed", f"MLX model not found: {model.ref.name}")
        try:
            from mlx_lm import load as mlx_load
        except (ImportError, OSError) as exc:
            raise EngineError(
                "backend_unavailable", f"MLX could not start: {exc}. {_INSTALL}"
            ) from exc
        try:
            weights, tokenizer = mlx_load(str(path))
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError("load_failed", str(exc) or "MLX load failed") from exc
        return ModelHandle(
            model=model,
            options=options or LoadOptions(),
            runtime=(weights, tokenizer),
        )

    def unload(self, handle: LoadedHandle) -> None:
        runtime = getattr(handle, "runtime", None)
        del runtime
        try:
            import mlx.core as mx
        except ImportError:
            return
        clearer = getattr(mx, "clear_cache", None)
        if callable(clearer):
            clearer()
        metal = getattr(mx, "metal", None)
        metal_clear = getattr(metal, "clear_cache", None)
        if callable(metal_clear):
            metal_clear()

    def stream_generate(
        self,
        handle: LoadedHandle,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        weights, tokenizer = _handle_runtime(handle)
        if cancel.is_set():
            return
        try:
            from mlx_lm import stream_generate as mlx_stream
        except ImportError as exc:
            raise EngineError("backend_unavailable", _UNAVAILABLE) from exc
        prompt = _chat_prompt(tokenizer, messages)
        kwargs: dict[str, object] = {"max_tokens": params.max_tokens}
        try:
            from mlx_lm.sample_utils import make_sampler

            kwargs["sampler"] = make_sampler(params.temperature, params.top_p)
        except ImportError:
            kwargs["temp"] = params.temperature
            kwargs["top_p"] = params.top_p
        try:
            for response in mlx_stream(weights, tokenizer, prompt, **kwargs):
                if cancel.is_set():
                    return
                text = _delta_text(response)
                if text:
                    yield text
        except EngineError:
            raise
        except Exception as exc:
            if cancel.is_set():
                return
            raise EngineError("load_failed", str(exc) or "MLX generate failed") from exc

    def delete(self, model: LocalModel) -> None:
        import shutil

        if model.path is None:
            raise EngineError("not_found", f"MLX model not found: {model.ref.name}")
        path = model.path.resolve()
        root = self._model_dir.resolve()
        if path == root or not path.is_relative_to(root):
            raise EngineError("load_failed", "refusing to delete path outside model dir")
        if not path.exists():
            raise EngineError("not_found", f"MLX model not found: {model.ref.name}")
        shutil.rmtree(path)
