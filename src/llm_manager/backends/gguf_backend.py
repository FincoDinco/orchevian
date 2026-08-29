from collections.abc import Generator
from pathlib import Path

from ..config import get_config
from ..models import LocalModel


class GGUFBackend:
    name = "gguf"

    def is_available(self) -> bool:
        try:
            import llama_cpp  # noqa: F401
            return True
        except ImportError:
            return False

    def list_models(self) -> list[LocalModel]:
        gguf_dir = get_config().gguf_dir
        if not gguf_dir.exists():
            return []
        models = []
        for path in sorted(gguf_dir.glob("*.gguf")):
            size = path.stat().st_size
            models.append(LocalModel(
                name=path.stem,
                backend="gguf",
                path=path,
                size=size,
                details={"file": path.name},
            ))
        return models

    def delete(self, model: LocalModel) -> None:
        if model.path and model.path.exists():
            model.path.unlink()

    def stream_chat(
        self,
        model: LocalModel,
        messages: list[dict],
        params: dict,
    ) -> Generator[str, None, None]:
        try:
            from llama_cpp import Llama
        except ImportError:
            raise RuntimeError(
                "llama-cpp-python is not installed.\n"
                "Install with: CMAKE_ARGS='-DLLAMA_METAL=on' pip install llama-cpp-python"
            )

        llm = Llama(
            model_path=str(model.path),
            n_ctx=params.get("max_tokens", 4096),
            verbose=False,
        )
        for chunk in llm.create_chat_completion(
            messages=messages,
            temperature=params.get("temperature", 0.7),
            top_p=params.get("top_p", 0.9),
            max_tokens=params.get("max_tokens", 2048),
            stream=True,
        ):
            delta = chunk["choices"][0].get("delta", {})
            if content := delta.get("content"):
                yield content
