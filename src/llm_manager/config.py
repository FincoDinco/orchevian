from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    model_dir: Path = field(default_factory=lambda: Path.home() / "models")
    db_path: Path = field(default_factory=lambda: Path.home() / ".local" / "share" / "llm-manager" / "data.db")
    api_host: str = "127.0.0.1"
    api_port: int = 8080

    @property
    def mlx_dir(self) -> Path:
        return self.model_dir / "mlx"

    @property
    def gguf_dir(self) -> Path:
        return self.model_dir / "gguf"

    def ensure_dirs(self) -> None:
        self.mlx_dir.mkdir(parents=True, exist_ok=True)
        self.gguf_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
        _config.ensure_dirs()
    return _config
