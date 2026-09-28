# Development

## Setup

Orchevian needs [Python 3.13](https://www.python.org/) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked --extra gui --extra mlx --extra gguf --group dev
```

This installs the runtimes required by the desktop smoke test: GGUF on every platform
and MLX on Apple Silicon (automatically omitted elsewhere). Running `uv sync` without
an extra removes that extra's packages.

## Checks

```bash
uv run --no-sync ruff check .
uv run --no-sync pytest -q
```

Some tests open local loopback servers; sandboxed environments may need to allow them.
GitHub Actions runs lint and tests on Ubuntu, Windows, and macOS with Python 3.13. Hardware tests cover CPU-only hosts, Apple Silicon/Intel Macs, Windows/Linux on x86 and ARM, NVIDIA/AMD memory probes, low-memory hosts, memory pressure, missing drivers, and detection failures. These tests use simulated hardware; actual model compatibility and inference performance still depend on each runtime, GPU driver, and model.

## Interface screenshots

To render screenshots of the real interface with temporary sample data and fake backends:

```bash
uv run python scripts/preview_ui.py --output /tmp/orchevian-preview
```

This preview does not access your conversations, installed models, or backend services. It covers both themes, welcome and chat states, model details, Second Brain capture, notes and graphs, and compact layouts.

## Real-model checks

These scripts use an installed model and a temporary library. They never open your
saved chats or download models.

Run a live provider and page-reading check without opening your library:

```sh
.venv/bin/python scripts/check_web_search.py "Python official documentation"
```

To exercise retrieval and an installed model together in a temporary chat library:

```sh
.venv/bin/python scripts/check_web_answer.py --model mlx/ORG/MODEL \
  --report /tmp/orchevian-web-answer.json "Your question"
```

Use a complete model ID from `orchevian-engine models`. This check saves the answer
and retrieved excerpts in its optional JSON report, requires a Markdown citation
to a retrieved URL, and verifies that a subsequent search-off turn performs no
retrieval. It does not open your saved chat library or download models. Review
the answer against the excerpts; a passing check does not prove factual accuracy.
For a question with an independently verified event date, add
`--expect-date YYYY-MM-DD`. This checks the visible answer, excluding dates found
only inside link targets. The expected date is never supplied to the model.

Run the real-model fixture with an already installed model and a new output directory:

```sh
.venv/bin/python scripts/check_artifact_workflow.py \
  --model mlx/ORG/MODEL --output dist/artifact-check --extended --max-tokens 7000
```

This creates a separate fixture library, records model prompts/responses, exports
the native files and previews, and writes `report.json`. It checks source facts,
numeric JSON budgets, real HTML tables, SVG labels, slide content/notes, spreadsheet
formulas/formatting/charts, PDF form filling and reopening, revisions, ZIP export,
project reuse, and persistence. It neither opens saved chats nor downloads models.

For broader acceptance across spreadsheets, slides, writing, and sourced answers:

```sh
.venv/bin/python scripts/check_model_matrix.py \
  --model mlx/ORG/MODEL --output dist/model-matrix --live-web
```

Omit `--live-web` to skip the two live public searches, or pass `--case NAME` to
run one case. It uses a temporary library, independently reads back the native
files, and writes `report.json`. Passing these bounded cases does not guarantee
general quality or native Office layout.

## Desktop builds

See [packaging/README.md](../packaging/README.md). The build script verifies the frozen
app with temporary data before producing a platform archive and checksum. The
**Desktop packages** workflow builds preview artifacts for macOS, Windows, and Linux
without publishing releases.

## Code layout

- `src/llm_engine`: engine library (models, chat, documents, web search, file
  creation, storage). It must not import PySide6.
- `src/llm_manager_app`: PySide6 desktop app. It talks to engine services only and
  must not import sqlite3, mlx_lm, llama_cpp, fastapi, or huggingface_hub.
- `tests/`: pytest suite.
- `scripts/`: interface previews, desktop builds, and real-model checks.
- `packaging/`: PyInstaller spec and desktop build notes.

[DESIGN.md](../DESIGN.md) describes the architecture and design decisions; the original rebuild plan is in [docs/history](history/redesign-plan.md).
