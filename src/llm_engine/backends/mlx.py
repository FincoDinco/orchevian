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
    text_config = raw.get("text_config")
    language = text_config if isinstance(text_config, dict) else raw
    mapping = (
        ("max_position_embeddings", "context_length"),
        ("hidden_size", "hidden_size"),
        ("num_hidden_layers", "num_layers"),
    )
    for src, dest in mapping:
        value = language.get(src, raw.get(src))
        if value is not None:
            details[dest] = str(value)
    architecture = language.get("model_type", raw.get("model_type"))
    if isinstance(architecture, str):
        details["architecture"] = architecture
    quant = raw.get("quantization") or raw.get("quantization_config")
    if isinstance(quant, dict):
        bits = quant.get("bits")
        if isinstance(bits, int) and not isinstance(bits, bool):
            details["quantization"] = f"{bits}-bit"
        if isinstance(quant.get("group_size"), int):
            details["quantization_group_size"] = str(quant["group_size"])
    dtype = language.get("dtype", language.get("torch_dtype", raw.get("dtype")))
    if isinstance(dtype, str):
        details["weight_dtype"] = dtype
    details["runtime"] = "MLX · Apple Silicon / Metal"
    return details


def _chat_prompt(tokenizer: object, messages: list[ChatTurn]) -> str:
    payload = [{"role": turn.role, "content": turn.content} for turn in messages]
    apply = getattr(tokenizer, "apply_chat_template", None)
    has_template = getattr(
        tokenizer, "has_chat_template", getattr(tokenizer, "chat_template", True)
    )
    if callable(apply) and has_template:
        # Match Ollama's direct-answer default. Qwen templates otherwise open a
        # thinking block that can consume the entire reply budget on long inputs.
        template = getattr(tokenizer, "chat_template", "")
        kwargs = {"enable_thinking": False} if "enable_thinking" in str(template) else {}
        return str(apply(payload, tokenize=False, add_generation_prompt=True, **kwargs))
    return "".join(f"{turn.role}: {turn.content}\n" for turn in messages) + "assistant: "


def _delta_text(response: object) -> str:
    if isinstance(response, str):
        return response
    text = getattr(response, "text", None)
    return text if isinstance(text, str) else ""


def _clean_stream(responses, tokenizer, cancel):
    """Keep EOS markers out of replies, including markers split across deltas."""
    eos = getattr(tokenizer, "eos_token", None)
    pending = ""
    try:
        for response in responses:
            if cancel.is_set():
                return
            pending += _delta_text(response)
            if not isinstance(eos, str) or not eos:
                if pending:
                    yield pending
                    pending = ""
                continue
            end = pending.find(eos)
            if end >= 0:
                if end:
                    yield pending[:end]
                return
            keep = next((n for n in range(min(len(eos) - 1, len(pending)), 0, -1)
                         if pending.endswith(eos[:n])), 0)
            ready = pending[:-keep] if keep else pending
            pending = pending[-keep:] if keep else ""
            if ready:
                yield ready
        if pending and not cancel.is_set():
            yield pending
    finally:
        close = getattr(responses, "close", None)
        if callable(close):
            close()


def _generation_input(handle, tokenizer, messages, params):
    prompt = _chat_prompt(tokenizer, messages)
    options = getattr(handle, "options", LoadOptions())
    limit = options.n_ctx
    native = handle.model.details.get("context_length", "")
    if native.isdigit() and int(native) > 0:
        limit = min(limit, int(native))
    kwargs = {"max_tokens": params.max_tokens, "max_kv_size": limit, "prefill_step_size": 512}
    encode = getattr(tokenizer, "encode", None)
    if callable(encode):
        bos = getattr(tokenizer, "bos_token", None)
        prompt = encode(prompt, add_special_tokens=not bos or not prompt.startswith(bos))
        if len(prompt) >= limit:
            raise EngineError(
                "context_full", f"This MLX chat needs {len(prompt):,} input tokens, exceeding "
                f"its {limit:,}-token context budget. Start a new chat or shorten the input.",
            )
        kwargs["max_tokens"] = min(params.max_tokens, limit - len(prompt))
    return prompt, kwargs


class MLXBackend:
    name = BackendName.MLX
    supports_offline_catalog = True

    def __init__(self, model_dir: Path | None = None) -> None:
        self._model_dir = Path(model_dir) if model_dir is not None else default_model_dir() / "mlx"

    def is_available(self) -> tuple[bool, str | None]:
        if not _is_apple_silicon():
            return False, _UNAVAILABLE
        if not _module_available("mlx_lm"):
            if getattr(sys, "frozen", False):
                return False, (
                    "This desktop build does not include the MLX runtime. "
                    "Use an Ollama model, or run Orchevian from source with the MLX extra."
                )
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
            # Model and tokenizer configs can specify different valid terminators.
            # Preserve both instead of letting a model config replace the tokenizer's EOS.
            eos = getattr(tokenizer, "eos_token_id", None)
            eos_ids = getattr(tokenizer, "eos_token_ids", None)
            if isinstance(eos, int) and isinstance(eos_ids, set):
                eos_ids.add(eos)
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
        if any(turn.images for turn in messages):
            raise EngineError("vision_required", "This backend currently supports text only. "
                              "Use an Ollama vision model for image interpretation.")
        weights, tokenizer = _handle_runtime(handle)
        if cancel.is_set():
            return
        try:
            from mlx_lm import stream_generate as mlx_stream
        except ImportError as exc:
            raise EngineError("backend_unavailable", _UNAVAILABLE) from exc
        prompt, kwargs = _generation_input(handle, tokenizer, messages, params)
        try:
            from mlx_lm.sample_utils import make_sampler

            kwargs["sampler"] = make_sampler(params.temperature, params.top_p)
        except ImportError:
            kwargs["temp"] = params.temperature
            kwargs["top_p"] = params.top_p
        try:
            yield from _clean_stream(
                mlx_stream(weights, tokenizer, prompt, **kwargs), tokenizer, cancel,
            )
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
