from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from llm_engine.domain.errors import EngineError

API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8080
ENV_DB = "LLM_ENGINE_DB"
ENV_CONFIG = "LLM_ENGINE_CONFIG"

_WRITABLE_FIELDS = frozenset({"model_dir", "api_port"})


def app_data_dir() -> Path:
    # Same XDG-style path on every OS; do not use APPDATA.
    return Path.home() / ".local" / "share" / "llm-manager"


def default_db_path() -> Path:
    return app_data_dir() / "data.db"


def default_config_path() -> Path:
    return app_data_dir() / "config.json"


def default_log_path() -> Path:
    return app_data_dir() / "logs" / "engine.log"


def default_model_dir() -> Path:
    return Path.home() / "models"


def resolve_db_path(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get(ENV_DB)
    if env:
        return Path(env).expanduser()
    return default_db_path()


def resolve_config_path(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get(ENV_CONFIG)
    if env:
        return Path(env).expanduser()
    return default_config_path()


@dataclass(frozen=True, slots=True)
class EngineConfig:
    model_dir: Path
    api_port: int
    api_host: str = API_HOST
    db_path: Path = field(default_factory=default_db_path)
    config_path: Path = field(default_factory=default_config_path)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_dir": str(self.model_dir),
            "api_port": self.api_port,
            "api_host": self.api_host,
            "db_path": str(self.db_path),
        }


def _parse_port(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise EngineError("config_invalid", f"invalid api_port: {value!r}")
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise EngineError("config_invalid", f"invalid api_port: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise EngineError("config_invalid", f"invalid api_port: {port}")
    return port


def _expand_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _ensure_model_subdirs(model_dir: Path) -> None:
    (model_dir / "mlx").mkdir(parents=True, exist_ok=True)
    (model_dir / "gguf").mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".config.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def load(
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> EngineConfig:
    path = resolve_config_path(config_path)
    db = resolve_db_path(db_path)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EngineError("config_invalid", f"invalid JSON in {path}") from exc
        if not isinstance(raw, dict):
            raise EngineError("config_invalid", "config.json must be an object")
        model_raw = raw.get("model_dir", default_model_dir())
        if not str(model_raw).strip():
            raise EngineError("config_invalid", "model_dir must be a non-empty path")
        model_dir = _expand_path(str(model_raw))
        api_port = _parse_port(raw.get("api_port", DEFAULT_API_PORT))
    else:
        model_dir = default_model_dir()
        api_port = DEFAULT_API_PORT
    return EngineConfig(
        model_dir=model_dir,
        api_port=api_port,
        api_host=API_HOST,
        db_path=db,
        config_path=path,
    )


def save(config: EngineConfig) -> None:
    _atomic_write_json(
        config.config_path,
        {
            "model_dir": str(config.model_dir),
            "api_port": config.api_port,
        },
    )
    _ensure_model_subdirs(config.model_dir)


def get(
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    return load(config_path, db_path).as_dict()


def set(
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
    **fields: Any,
) -> EngineConfig:
    unknown = frozenset(fields) - _WRITABLE_FIELDS
    if unknown:
        raise EngineError("config_invalid", f"unknown fields: {sorted(unknown)}")
    cfg = load(config_path, db_path)
    if "model_dir" in fields:
        value = fields["model_dir"]
        if not str(value).strip():
            raise EngineError("config_invalid", "model_dir must be a non-empty path")
        cfg = replace(cfg, model_dir=_expand_path(value))
    if "api_port" in fields:
        cfg = replace(cfg, api_port=_parse_port(fields["api_port"]))
    save(cfg)
    return cfg


def ensure_initialized(
    config_path: str | Path | None = None,
    db_path: str | Path | None = None,
) -> EngineConfig:
    cfg = load(config_path, db_path)
    if not cfg.config_path.is_file():
        save(cfg)
    else:
        _ensure_model_subdirs(cfg.model_dir)
    return cfg
