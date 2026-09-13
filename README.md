# Orchevian

Orchevian is the new name for LLM Manager. Existing chats, models, project settings, and Second Brain remain available.

Local-first desktop app for discovering, loading, and chatting with large language models on this machine.

This repository is a greenfield rebuild. **`llm_engine`** is a Python library with no GUI imports. **`llm_manager_app`** is a PySide6 GUI that talks only to engine services.

v1 launches with `uv run orchevian`. There is no bundled runtime gate (PyInstaller/briefcase is v1.1). The old Qt app is left installed as rollback.

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
uv sync --extra gui --extra mlx --extra gguf --group dev  # Both on Apple Silicon
```

## Launch

**Quit the old Qt app before live use.** Both apps share the same `data.db`. Do not run them at the same time. Do not delete `data.db`.

```bash
uv run orchevian          # GUI
uv run orchevian-engine --help    # engine CLI
uv run orchevian-engine chat      # TTY fallback
```

Opening the GUI migrates `data.db` in place (and writes `data.db.bak-pre-engine` on first engine open of a pre-engine database).

## Workspace

Projects and conversations share one sidebar. Chat uses a centered reading area and an integrated composer; open **Chat settings** for chat guidance and response settings. **Models** opens a searchable library with model details and load/unload actions. Ctrl/⌘F focuses search in the current workspace. **View → Hide Sidebar / Show Sidebar** gives the conversation more room (Control-Command-S on Mac; Ctrl+Shift+S elsewhere).

To create a project, use the **+** beside **Projects** or **File → New Project**. The form opens in the center workspace, with the same sidebar and theme. Add a name, optional **Project guidance**, and a model (or **Use app default**). **Get more models…** opens the model browser in the same workspace and preserves your draft; **Back to project** returns to it.

Selecting a project opens its home in the center workspace: a composer, its conversations, and a **Project guidance** card. Send from the home composer to start a chat in that project. Each project's unsent home draft and model choice are kept separately during the app session. **Edit project…** edits the name, guidance, and default model in the home; guidance and model changes apply to new chats only. Selecting the project again returns from a conversation to its home. Project file attachments and file context are not implemented.

**Settings → Models → Default model for new chats** chooses the app-wide fallback. An explicit model choice wins, then a project default, then the app default. Existing conversations keep their own model. Defaults retain the complete Ollama, GGUF, or MLX reference; a missing model or unavailable runtime is shown without silently substituting another model. Choosing a default does not load it.

Models use short display names, such as **Qwen 3 · 8B**, grouped under Ollama, GGUF, and MLX. Select a downloaded model and choose **Edit model…** to edit its display name in place. Save commits the name; Cancel discards the edit; a blank name restores the suggestion. Names are saved locally and appear in chats and project selection too. Renaming does not move model files or change existing conversation references. Original identifiers remain in the editor, tooltips, and model details.

In **Models → Downloaded**, select **Delete model…** to remove a model from disk (or from Ollama). Deletion asks for confirmation, unloads the selected model first, and keeps conversations. Stop any active generation before deleting.

**Models → Get more models** opens with suggestions estimated to fit your computer. Search by name, select a model, and choose **Get model** to check its download size. Select **Download** to install it, then **Downloads → Open model** to load it and start chatting. Turn off **Suggested for my computer** to search beyond the estimated memory budget.

The **Downloads** button in the top-right toolbar opens a compact panel over your current workspace. Its count shows unfinished downloads. Expand a row to see the repository, version, file count, and size; cancel, retry, and open actions stay within reach. Escape or a click outside closes the panel while downloads continue. Up to three models download at once; additional downloads wait in the queue. Queued entries display their status without a progress bar; cancellation shows activity while partial files are removed.

The sidebar holds New chat, search, its collapse toggle, and a **+** beside **Projects**. The downloads toolbar sits inside the main workspace so it does not interrupt the sidebar. The standard native titlebar and window frame remain available for dragging, resizing, minimizing, and maximizing. In Downloaded, **Model Details** expands technical information and model management actions; the model name and primary chat action stay visible.

**Options…** holds model format, an optional memory limit, hardware details, and the Hugging Face read token. Automatic format and memory settings are the default. **Model details** shows the original repository name, technical estimates, and the model-page link. When several downloads are available, a default is selected (preferring Q4 GGUF files); **Change version** exposes the alternatives.

Downloads show file sizes before starting, pin files to one repository revision, check disk space, and verify file lengths and published SHA-256 checksums when available. GGUF offers individual quantizations, including complete split-file sets; MLX installs weights, configuration, and tokenizer assets. Downloads run in the background with progress and cancellation. Partial files stay hidden from Downloaded and are removed on cancellation or failure; successful downloads refresh the library automatically. Split GGUF files appear as one model and are deleted together. Cancelled downloads restart from the beginning when retried.

For gated models, accept access on the Hugging Face model page and enter a read token in **Options…**. Tokens entered there are kept only for the current app session; `HF_TOKEN` is also supported. The model-page link remains available for manual downloads. Downloading does not load or execute a model.

Recommendations run on **macOS, Windows, and Linux**, using [psutil](https://psutil.readthedocs.io/) for total and currently available RAM. **Auto format** chooses MLX on Apple Silicon and GGUF elsewhere, including Intel Macs and CPU-only systems. NVIDIA memory is probed with `nvidia-smi` on Windows/Linux; AMD VRAM is read from Linux DRM when available. Unrecognized GPUs (including AMD/Intel on Windows) fall back to system-RAM recommendations. Detecting a GPU does not establish that the installed runtime supports it. Results distinguish GPU-memory fit from CPU-memory fit; CPU inference may be slow.

Apple unified memory is counted once, and separate GPUs are evaluated individually without summing their VRAM. Busy machines receive a smaller budget. If detection fails, search still works and **Options… → Memory limit** lets users supply a limit; otherwise that control can reduce the detected budget. Hardware information stays on the machine; Hugging Face receives the search query and format, not the hardware profile. Search uses the [public Hugging Face API](https://huggingface.co/docs/hub/api).

Place downloaded GGUF files under `model_dir/gguf/`; put complete MLX repositories (weights, configuration, and tokenizer) under `model_dir/mlx/`. Nested owner/repository folders are supported. Refresh Downloaded after downloading. Local files remain visible and deletable when an optional runtime is missing; loading is disabled with setup instructions.

**Settings** stays inside the main workspace. Open it from the sidebar or Ctrl/⌘, and use **Back to chats** or the sidebar to return.

**Settings → General → Automatically remember chats** is on by default. After a completed regular response, the local model selects durable facts, preferences, decisions, and useful ideas, filters out unhelpful or repeated material, and adds connected notes to Second Brain. Notes require exact conversation evidence and remain editable. Capture runs when the model is idle and briefly uses the same model session; the status bar shows its progress and supports stopping it. Turning the setting off cancels automatic capture and leaves existing notes intact. It applies to new completed responses, without scanning old chats on startup.

**Private Chat** opens from the top-right toolbar beside Downloads (Ctrl/⌘Shift+P also opens it). It fills the app workspace, hides the sidebar and toolbar, and stays active until you select **Clear private chat**. Navigation shortcuts are disabled during private chat. Only messages from the current private chat provide context: saved conversations, project guidance, and Second Brain are excluded. Private messages never enter the conversation database or memory vault. Clearing the chat or closing the app stops the response and erases its transcript and unsent draft. Clearing returns to your regular workspace and restores your regular draft; reopening private chat starts empty.

**Second Brain** stores editable Markdown notes with `[[wiki links]]`, backlinks, search, and an interactive graph. Create notes yourself or select **Remember chat** to extract up to six memories using the conversation's local model. Generated notes include an evidence quote, model attribution, and a linked snapshot of the conversation. Repeating capture on an already saved conversation version reuses its memories.

Notes live in `second-brain/` beside your database by default. **Choose vault…** opens another folder, including an existing Markdown vault. Notes can be edited outside the app; select **Refresh notes** to pick up changes. Saving reports a conflict when a note has changed on disk. Deleted notes move to the vault's `.trash/` folder.

**Use relevant memories in chats** is enabled by default. It adds up to four notes matched by keywords to the model's context, excluding source transcripts. Turn it off in Second Brain to chat without recalled notes. Memory capture runs in the background and can be cancelled.

To render screenshots of the real interface with temporary sample data and fake backends:

```bash
uv run python scripts/preview_ui.py --output /tmp/orchevian-preview
```

This preview does not access your conversations, installed models, or backend services. It covers both themes, welcome and chat states, model details, Second Brain capture, notes and graphs, and compact layouts.

## Backends

| Backend | Availability |
| --- | --- |
| Ollama | First-class, all OSes |
| GGUF | Optional extra `gguf` (`llama-cpp-python`), all OSes |
| MLX | Optional extra `mlx` (`mlx-lm`), Darwin/arm64 only |

A missing extra is shown as unavailable. It does not crash the app.

On Apple Silicon, keep both extras installed with `uv sync --extra gui --extra mlx --extra gguf`. Running `uv sync` without the extras removes their optional packages. GGUF uses full GPU offload when the installed llama.cpp build supports it and estimated model memory fits the detected GPU budget; otherwise it uses CPU. MLX requires a macOS session with access to Metal. The MLX extra is platform-gated, so it is skipped on Windows, Linux, and Intel Macs.

## Development checks

```bash
uv sync --extra gui --group dev
uv run ruff check src tests
uv run pytest -q
```

GitHub Actions runs lint and tests on Ubuntu, Windows, and macOS with Python 3.13. Hardware tests cover CPU-only hosts, Apple Silicon/Intel Macs, Windows/Linux on x86 and ARM, NVIDIA/AMD memory probes, low-memory hosts, memory pressure, missing drivers, and detection failures. These tests use simulated hardware; actual model compatibility and inference performance still depend on each runtime, GPU driver, and model.

## Stopping a stuck model

Select **Cancel loading** in the model library, or use the **Force stop model** button at the bottom of the window while a model is working. It remains available when you switch workspaces. **Escape** stops the current load, chat response, or memory capture; **Ctrl/⌘Shift+.** force-stops the active model operation.

Model runtimes run in separate worker processes so a blocked native loader can be terminated without waiting for it to return or closing the app. Loads and waits for the first response time out after 120 seconds. Stopping preserves your conversation and any partial response; you can then choose a smaller model or retry. A stopped runtime is loaded again for the next request.

For Ollama, stopping also asks its service to release the model using [`keep_alive: 0`](https://docs.ollama.com/api/generate). If the service does not confirm unloading within five seconds, the app reports the failure and shows a recovery command. An unresponsive external Ollama service may need `ollama stop MODEL_NAME` or an Ollama restart.

## Data paths

Same XDG-style location on every OS (not `%APPDATA%`):

| File | Default |
| --- | --- |
| Config (new installs) | `~/.local/share/orchevian/config.json` |
| Database (new installs) | `~/.local/share/orchevian/data.db` |
| Logs (new installs) | `~/.local/share/orchevian/logs/engine.log` |

If `~/.local/share/llm-manager/` already exists, Orchevian continues using it in place, including its existing Second Brain vault. GUI preferences and custom model names are imported once into Orchevian's settings, without replacing newer values.

Override with `--config` / `--db`, or `ORCHEVIAN_CONFIG` / `ORCHEVIAN_DB`. The older `LLM_ENGINE_CONFIG` / `LLM_ENGINE_DB` variables remain supported; Orchevian variables take precedence. The `llm-manager` and `llm-engine` commands remain compatibility aliases for `orchevian` and `orchevian-engine`. Python imports stay `llm_manager_app` and `llm_engine` for compatibility.

`config.json` stores `model_dir` (default `~/models`) and `api_port` (default `8080`). The API host is always `127.0.0.1`.

## Engine CLI

```bash
uv run orchevian-engine --help
uv run orchevian-engine health
uv run orchevian-engine models
```

Subcommands: `chat`, `models`, `serve`, `migrate`, `health`.

## Layout

- `src/llm_engine` — engine library (must not import PySide6)
- `src/llm_manager_app` — PySide6 GUI (must not import sqlite3 / mlx_lm / llama_cpp / fastapi / huggingface_hub)
- `tests/` — pytest
