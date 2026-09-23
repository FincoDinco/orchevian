from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from llm_engine.cli import main
from llm_engine.config import (
    API_HOST,
    DEFAULT_API_PORT,
    EngineConfig,
    default_config_path,
    default_db_path,
    default_model_dir,
    ensure_initialized,
    get,
    load,
    resolve_config_path,
    resolve_db_path,
    save,
    set,
)
from llm_engine.domain.errors import EngineError


def test_default_db_path_is_xdg_on_all_oses(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # A new install uses Orchevian's folder...
    assert default_db_path() == tmp_path / ".local" / "share" / "orchevian" / "data.db"
    # ...while an existing LLM Manager library keeps being used in place.
    (tmp_path / ".local" / "share" / "llm-manager").mkdir(parents=True)
    path = default_db_path()
    assert path.parts[-4:] == (".local", "share", "llm-manager", "data.db")


def test_default_paths_ignore_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("APPDATA", r"C:\Users\seth\AppData\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\seth\AppData\Local")
    db = default_db_path()
    cfg = default_config_path()
    # Only the part below home matters: Windows keeps temporary folders in AppData.
    assert "AppData" not in db.relative_to(tmp_path).parts
    assert "AppData" not in cfg.relative_to(tmp_path).parts
    assert cfg == tmp_path / ".local" / "share" / "orchevian" / "config.json"


def test_env_overrides_db_and_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db = tmp_path / "custom.db"
    cfg = tmp_path / "custom.json"
    monkeypatch.setenv("LLM_ENGINE_DB", str(db))
    monkeypatch.setenv("LLM_ENGINE_CONFIG", str(cfg))
    assert resolve_db_path() == db
    assert resolve_config_path() == cfg


def test_cli_path_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_ENGINE_DB", str(tmp_path / "env.db"))
    monkeypatch.setenv("LLM_ENGINE_CONFIG", str(tmp_path / "env.json"))
    cli_db = tmp_path / "cli.db"
    cli_cfg = tmp_path / "cli.json"
    assert resolve_db_path(cli_db) == cli_db
    assert resolve_config_path(cli_cfg) == cli_cfg


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "data.db"
    cfg = load(cfg_path, db_path)
    assert cfg.model_dir == default_model_dir()
    assert cfg.api_port == DEFAULT_API_PORT
    assert cfg.api_host == API_HOST
    assert cfg.db_path == db_path
    assert cfg.config_path == cfg_path
    assert not cfg_path.exists()


def test_save_roundtrip_does_not_persist_host(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "data.db"
    model_dir = tmp_path / "models"
    original = EngineConfig(
        model_dir=model_dir,
        api_port=9090,
        db_path=db_path,
        config_path=cfg_path,
    )
    save(original)
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert raw == {"model_dir": str(model_dir), "api_port": 9090}
    assert "api_host" not in raw
    loaded = load(cfg_path, db_path)
    assert loaded.model_dir == model_dir
    assert loaded.api_port == 9090
    assert loaded.api_host == "127.0.0.1"


def test_save_creates_backend_dirs_and_mode(tmp_path: Path) -> None:
    model_dir = tmp_path / "models"
    cfg_path = tmp_path / "config.json"
    save(
        EngineConfig(
            model_dir=model_dir,
            api_port=8080,
            db_path=tmp_path / "data.db",
            config_path=cfg_path,
        )
    )
    assert (model_dir / "mlx").is_dir()
    assert (model_dir / "gguf").is_dir()
    assert cfg_path.is_file()
    if os.name != "nt":
        mode = cfg_path.stat().st_mode & 0o777
        assert mode == 0o600


def test_set_and_get(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "data.db"
    model_dir = tmp_path / "weights"
    set(cfg_path, db_path, model_dir=model_dir, api_port=8123)
    data = get(cfg_path, db_path)
    assert data["model_dir"] == str(model_dir)
    assert data["api_port"] == 8123
    assert data["api_host"] == "127.0.0.1"
    assert data["db_path"] == str(db_path)


def test_set_rejects_api_host(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="unknown fields") as exc:
        set(tmp_path / "config.json", tmp_path / "data.db", api_host="0.0.0.0")
    assert exc.value.code == "config_invalid"


def test_invalid_port_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"model_dir": "/tmp/models", "api_port": 0}\n', encoding="utf-8")
    with pytest.raises(EngineError) as exc:
        load(cfg_path, tmp_path / "data.db")
    assert exc.value.code == "config_invalid"

    cfg_path.write_text('{"model_dir": "/tmp/models", "api_port": "nope"}\n', encoding="utf-8")
    with pytest.raises(EngineError) as exc:
        load(cfg_path, tmp_path / "data.db")
    assert exc.value.code == "config_invalid"


def test_invalid_json_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(EngineError) as exc:
        load(cfg_path, tmp_path / "data.db")
    assert exc.value.code == "config_invalid"


def test_tilde_in_model_dir_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() may be cached; expanduser still honors HOME.
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text('{"model_dir": "~/weights", "api_port": 8080}\n', encoding="utf-8")
    cfg = load(cfg_path, tmp_path / "data.db")
    assert cfg.model_dir == Path(os.path.expanduser("~/weights"))


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_migrate_command_initializes_database_without_changing_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "data.db"
    assert main(["migrate", "--config", str(cfg_path), "--db", str(db_path)]) == 0
    assert db_path.exists()
    assert not cfg_path.exists()


def test_ensure_initialized_writes_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("llm_engine.config.default_model_dir", lambda: tmp_path / "models")
    cfg_path = tmp_path / "config.json"
    db_path = tmp_path / "data.db"
    cfg = ensure_initialized(cfg_path, db_path)
    assert cfg_path.is_file()
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert "api_host" not in raw
    assert raw["api_port"] == 8080
    assert cfg.api_host == "127.0.0.1"
    assert cfg.model_dir == tmp_path / "models"
    assert (tmp_path / "models" / "mlx").is_dir()
