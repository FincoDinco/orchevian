# LLM Manager

Local-first desktop app for discovering, loading, and chatting with large language models on this machine.

This repository is a greenfield rebuild. **`llm_engine`** is a Python library with no GUI imports. **`llm_manager_app`** is a PySide6 GUI that talks only to engine services.

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync --extra gui --group dev
```

## Launch

```bash
uv run llm-manager          # GUI (three-column shell)
uv run llm-engine --help    # engine CLI
```

## Engine CLI

```bash
uv run llm-engine --help
```

Subcommands: `chat`, `models`, `serve`, `migrate`, `health`.

`llm-engine models` lists registry backends (Ollama) and prints unavailable reasons on stderr. Other subcommands are stubs until later engine PRs.

```bash
uv run llm-engine health
```

## Layout

- `src/llm_engine` — engine library (must not import PySide6)
- `src/llm_manager_app` — PySide6 GUI (must not import sqlite3 / mlx_lm / llama_cpp)
- `tests/` — pytest

## Data paths

Same XDG-style location on every OS (not `%APPDATA%`):

| File | Default |
| --- | --- |
| Config | `~/.local/share/llm-manager/config.json` |
| Database | `~/.local/share/llm-manager/data.db` |
| Logs | `~/.local/share/llm-manager/logs/engine.log` |

Override with `--config` / `--db`, or `LLM_ENGINE_CONFIG` / `LLM_ENGINE_DB`.

`config.json` stores `model_dir` (default `~/models`) and `api_port` (default `8080`). The API host is always `127.0.0.1`.
