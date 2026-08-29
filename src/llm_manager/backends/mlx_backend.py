import sys
from collections.abc import Generator
from pathlib import Path

from ..config import get_config
from ..models import LocalModel


class MLXBackend:
    name = "mlx"

    def is_available(self) -> bool:
        try:
            import mlx_lm  # noqa: F401
            return True
        except ImportError:
            return False

    def list_models(self) -> list[LocalModel]:
        mlx_dir = get_config().mlx_dir
        if not mlx_dir.exists():
            return []
        models = []
        for path in sorted(mlx_dir.iterdir()):
            if not path.is_dir():
                continue
            # A valid MLX model dir has weights files
            has_weights = any(path.glob("*.safetensors")) or any(path.glob("*.npz"))
            if not has_weights:
                continue
            size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
            details: dict = {}
            config_file = path / "config.json"
            if config_file.exists():
                import json
                try:
                    cfg = json.loads(config_file.read_text())
                    details["context_length"] = cfg.get("max_position_embeddings", "")
                    details["hidden_size"] = cfg.get("hidden_size", "")
                    details["num_layers"] = cfg.get("num_hidden_layers", "")
                except Exception:
                    pass
            models.append(LocalModel(
                name=path.name,
                backend="mlx",
                path=path,
                size=size,
                details=details,
            ))
        return models

    def delete(self, model: LocalModel) -> None:
        import shutil
        if model.path and model.path.exists():
            shutil.rmtree(model.path)

    def stream_chat(
        self,
        model: LocalModel,
        messages: list[dict],
        params: dict,
    ) -> Generator[str, None, None]:
        try:
            from mlx_lm import load, stream_generate
        except ImportError:
            raise RuntimeError("mlx_lm is not installed. Run: uv add mlx-lm")

        lm_model, tokenizer = load(str(model.path))
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        for response in stream_generate(
            lm_model,
            tokenizer,
            prompt=prompt,
            max_tokens=params.get("max_tokens", 2048),
            temp=params.get("temperature", 0.7),
            top_p=params.get("top_p", 0.9),
        ):
            if hasattr(response, "text"):
                yield response.text
            elif isinstance(response, str):
                yield response
