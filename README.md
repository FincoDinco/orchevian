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

## Workspace

Projects and conversations share one sidebar. Chat uses a centered reading area and an integrated composer; open **Chat settings** for instructions and generation controls. **Models** opens a searchable library with model details and load/unload actions. Ctrl/⌘F focuses search in the current workspace.

**Second brain** stores editable Markdown notes with `[[wiki links]]`, backlinks, search, and an interactive graph. Create notes yourself or select **Remember chat** to extract up to six memories using the conversation's local model. Generated notes include an evidence quote, model attribution, and a linked snapshot of the conversation. Repeating capture on an already saved conversation version reuses its memories.

Notes live in `second-brain/` beside your database by default. **Choose vault…** opens another folder, including an existing Markdown vault. Notes can be edited outside the app; select **Refresh notes** to pick up changes. Saving reports a conflict when a note has changed on disk. Deleted notes move to the vault's `.trash/` folder.

**Use relevant memories in chats** is enabled by default. It adds up to four notes matched by keywords to the model's context, excluding source transcripts. Turn it off in Second brain to chat without recalled notes. Memory capture runs in the background and can be cancelled.

To render screenshots of the real interface with temporary sample data and fake backends:

```bash
uv run python scripts/preview_ui.py --output /tmp/llm-manager-preview
```

This preview does not access your conversations, installed models, or backend services. It covers both themes, welcome and chat states, model details, Second brain capture, notes and graphs, and compact layouts.

## Backends

| Backend | Availability |
| --- | --- |
| Ollama | First-class, all OSes |
| GGUF | Optional extra `gguf` (`llama-cpp-python`), all OSes |
| MLX | Optional extra `mlx` (`mlx-lm`), Darwin/arm64 only |

A missing extra is shown as unavailable. It does not crash the app.

## Stopping a stuck model

Select **Cancel loading** in the model library, or use the **Force stop model** button at the bottom of the window while a model is working. It remains available when you switch workspaces. **Escape** stops the current load, chat response, or memory capture; **Ctrl/⌘Shift+.** force-stops the active model operation.

Model runtimes run in separate worker processes so a blocked native loader can be terminated without waiting for it to return or closing the app. Loads and waits for the first response time out after 120 seconds. Stopping preserves your conversation and any partial response; you can then choose a smaller model or retry. A stopped runtime is loaded again for the next request.

For Ollama, stopping also asks its service to release the model using [`keep_alive: 0`](https://docs.ollama.com/api/generate). If the service does not confirm unloading within five seconds, the app reports the failure and shows a recovery command. An unresponsive external Ollama service may need `ollama stop MODEL_NAME` or an Ollama restart.

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
