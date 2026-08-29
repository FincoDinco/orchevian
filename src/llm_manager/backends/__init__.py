from .mlx_backend import MLXBackend
from .ollama_backend import OllamaBackend
from .gguf_backend import GGUFBackend
from ..models import LocalModel

_mlx = MLXBackend()
_ollama = OllamaBackend()
_gguf = GGUFBackend()

ALL_BACKENDS = [_mlx, _ollama, _gguf]


def get_backend(name: str) -> MLXBackend | OllamaBackend | GGUFBackend:
    for b in ALL_BACKENDS:
        if b.name == name:
            return b
    raise ValueError(f"Unknown backend: {name!r}")


def list_all_models() -> list[LocalModel]:
    models = []
    for b in ALL_BACKENDS:
        try:
            models.extend(b.list_models())
        except Exception:
            pass
    return models
