# LLM Manager

Local-first desktop app for discovering, loading, and chatting with large language models on this machine.

This repository is a greenfield rebuild. **`llm_engine`** is a Python library with no GUI imports. **`llm_manager_app`** is a PySide6 GUI that talks only to engine services.

v1 launches with `uv run llm-manager`. There is no bundled runtime gate (PyInstaller/briefcase is v1.1). The old Qt app is left installed as rollback.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- [Ollama](https://ollama.com) for the first-class backend

## Setup

```bash
uv sync --extra gui --group dev
```

Optional extras:

```bash
uv sync --extra gui --extra gguf --group dev   # GGUF, all OSes
uv sync --extra gui --extra mlx --group dev    # MLX, Darwin/arm64 only
```

## Launch

**Quit the old Qt app before live use.** Both apps share the same `data.db`. Do not run them at the same time. Do not delete `data.db`.

```bash
uv run llm-manager          # GUI
uv run llm-engine --help    # engine CLI
uv run llm-engine chat      # TTY fallback
```

Opening the GUI migrates `data.db` in place (and writes `data.db.bak-pre-engine` on first engine open of a pre-engine database).

## Backends

| Backend | Availability |
| --- | --- |
| Ollama | First-class, all OSes |
| GGUF | Optional extra `gguf` (`llama-cpp-python`), all OSes |
| MLX | Optional extra `mlx` (`mlx-lm`), Darwin/arm64 only |

A missing extra is shown as unavailable. It does not crash the app.

## Data paths

Same XDG-style location on every OS (not `%APPDATA%`):

| File | Default |
| --- | --- |
| Config | `~/.local/share/llm-manager/config.json` |
| Database | `~/.local/share/llm-manager/data.db` |
| Logs | `~/.local/share/llm-manager/logs/engine.log` |

Override with `--config` / `--db`, or `LLM_ENGINE_CONFIG` / `LLM_ENGINE_DB`.

`config.json` stores `model_dir` (default `~/models`) and `api_port` (default `8080`). The API host is always `127.0.0.1`.

## Engine CLI

```bash
uv run llm-engine --help
uv run llm-engine health
uv run llm-engine models
```

Subcommands: `chat`, `models`, `serve`, `migrate`, `health`.

## Layout

- `src/llm_engine` — engine library (must not import PySide6)
- `src/llm_manager_app` — PySide6 GUI (must not import sqlite3 / mlx_lm / llama_cpp / fastapi / huggingface_hub)
- `tests/` — pytest
