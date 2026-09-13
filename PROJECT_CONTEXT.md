# Orchevian — Project Context for AI Brainstorming

Snapshot: September 12, 2026. Based on the current working tree, including work that may not yet be committed or released. This document is self-contained so it can be shared with another AI without sharing the repository.

## What this project is

**Orchevian is a local-first desktop AI workspace for discovering, downloading, running, and chatting with language models on your own computer.** It also organizes conversations into projects and turns useful information from chats into a personal, editable Markdown knowledge base called **Second Brain**.

This snapshot includes app-wide default models, in-shell project creation/home, and inline display-name editing in the working tree.

The project was previously called **LLM Manager**. Orchevian is the current product name; the repository and Python package names still contain the older name for compatibility.

The practical idea is to bring model management, everyday AI conversations, and personal knowledge together in one calm desktop interface. Users can choose their own local models, retain their conversations and notes locally, and reuse relevant knowledge in later chats.

This is an actively developed personal project owned by Seth Hardin. The package version is currently `0.1.0`. It is a working desktop application launched from a Python development environment; a polished installer and bundled runtime are future work.

## Who it is for and what problems it addresses

The documented starting audience is a technically comfortable user who already runs local models, especially through Ollama, and wants an approachable desktop workspace. The interface increasingly helps users who do not want to understand every model format or hardware setting before getting started.

Key jobs the application supports:

- Find a model that is likely to fit the computer's available memory.
- Download and manage models without switching between several tools.
- Chat with different local models through a consistent interface.
- Keep related conversations together with shared project guidance.
- Preserve useful facts, preferences, decisions, and ideas beyond a single chat.
- Have a temporary private conversation without adding it to saved history or memory.
- Recover from slow or stuck model operations without restarting the whole app.

Broader audiences, commercialization, and a definitive market position are open brainstorming topics, not established product decisions.

## What is implemented today

### 1. Local model discovery and management

- Supports **Ollama**, **GGUF through llama-cpp-python**, and **MLX through mlx-lm**.
- Ollama is the first-class backend. GGUF and MLX runtimes are optional dependencies; missing runtimes appear as unavailable rather than crashing the app.
- Searches Hugging Face and offers suggestions based on available system memory and detected GPU memory.
- Automatically favors MLX on Apple Silicon and GGUF elsewhere. Users can change format and memory settings.
- Shows download choices, file sizes, and technical details before installation.
- Downloads in the background, with up to three concurrent transfers, queued jobs, progress, cancellation, and retry.
- Handles split GGUF files as one model and installs the required files for MLX repositories.
- Checks disk space, pins downloads to a repository revision, and verifies file lengths and published checksums when available.
- Provides a searchable downloaded-model library with load, unload, reveal-on-disk, and confirmed deletion actions.
- Uses friendly names such as `Qwen 3 · 8B`. An inline **Edit model** flow saves custom display names without changing model files or conversation references.
- Settings provides an app-wide default model. Explicit choices override project defaults, which override the app default; missing saved models remain identifiable rather than being silently replaced.

Hardware fit is an estimate. It does not guarantee that a model will load successfully, run quickly, or use the GPU with the user's installed runtime.

### 2. Chat workspace

- A unified sidebar holds projects, recent conversations, and navigation.
- Chat has a centered reading area, integrated composer, and model picker grouped by backend.
- Supports streaming responses, stopping generation, regenerating a response, and preserving partial output when a response is interrupted.
- Renders completed responses as Markdown, including code blocks.
- Collapses explicit model-generated thinking sections behind a disclosure control when recognized tags are present. This displays text emitted by the model; it is not a separate reasoning engine.
- Provides per-chat guidance/system prompts and response-generation settings.
- Includes conversation search, rename, deletion, and moving chats between projects.
- Offers light and dark themes, keyboard shortcuts, a hideable sidebar, and settings inside the main workspace.

### 3. Projects

A project groups related chats and supplies a default model and guidance for new conversations. For example, a user can create a writing project with a preferred model and instructions about tone.

Project creation takes place inside the main workspace. Users can choose a downloaded model or inherit the app default, browse models, and return without losing the creation draft. Selecting a project opens an in-shell home with its own composer, conversation list, and editable guidance card. Home drafts and model choices are kept separately per project during the app session. Submitting creates a regular project chat. Editing project defaults affects new chats; existing conversations retain their settings. File references, attachments, and file context are not implemented.

### 4. Second Brain

Second Brain is a local folder of editable Markdown notes, with:

- A note reader and editor.
- `[[wiki links]]`, backlinks, search, and an interactive graph.
- Manual note creation and model-assisted extraction from conversations.
- Automatic extraction after completed regular responses, enabled by default and configurable.
- Up to six extracted memories per capture, with exact conversation evidence, model attribution, and linked source snapshots.
- Deduplication for repeat capture of an unchanged saved conversation.
- Optional recall, enabled by default, that adds up to four relevant notes to chat context using keyword matching.
- A selectable vault folder, support for externally edited Markdown, save-conflict detection, and a local trash folder for deleted notes.

Memory capture uses the local model and shares its session with chat. Current recall is **keyword-based**, not embedding/vector search. Evidence checks provide traceability but do not guarantee that the model's interpretation of a conversation is correct.

### 5. Private Chat

Private Chat is an isolated temporary conversation that fills the workspace until cleared.

- Its messages are not written to the conversation database or Second Brain vault.
- It does not recall saved memories, saved chats, or project guidance.
- Only the current private conversation supplies its chat context.
- Clearing it or closing the app removes its transcript and unsent draft from application state.
- Returning to the regular workspace restores the regular chat draft.

This describes the application's storage and context behavior. It is not a claim about encrypted storage, secure memory erasure, or every possible runtime/operating-system trace.

### 6. Model reliability and recovery

Production model loading and inference run in disposable worker processes so a blocked runtime can be terminated. The UI includes cancel-loading and global force-stop controls. Loading and waiting for the first response have timeouts.

The engine maintains one loaded model session and permits one generation operation at a time. Memory extraction shares that session. Concurrent model downloads do not imply concurrent inference.

## Architecture and technology

| Area | Current approach |
| --- | --- |
| Language and environment | Python 3.13+, managed with `uv` |
| Desktop interface | PySide6 / Qt 6 |
| Platform scope | macOS, Windows, and Linux; MLX specifically requires Apple Silicon macOS |
| Engine package | `llm_engine`: domain types, backend adapters, services, persistence, and hardware detection |
| GUI package | `llm_manager_app`: widgets, application shell, themes, and Qt worker adapters |
| GUI-to-engine communication | Direct Python service calls with worker threads/signals for background work |
| Runtime isolation | Disposable spawned processes for production model loading and generation |
| Conversations and projects | Local SQLite database |
| Personal knowledge | Local Markdown vault |
| Preferences | Qt `QSettings`; engine configuration in `config.json` |
| Development checks | pytest and Ruff; CI configured for Ubuntu, Windows, and macOS |

The engine must remain independent of Qt. GUI code calls services rather than directly accessing SQLite or importing inference libraries. This separation is a deliberate response to an older implementation that mixed interface, storage, and model execution too tightly.

New installations use `~/.local/share/orchevian/` for application data. Existing installations can continue using the legacy `~/.local/share/llm-manager/` directory. The default model directory is `~/models`. Existing user data and compatibility matter when making changes.

Typical development launch:

```sh
uv sync --extra gui --group dev
uv run orchevian
```

Optional runtime extras are `mlx` and `gguf`.

## Local-first and network boundaries

Conversations, project data, preferences, and notes are stored locally. Inference uses local model runtimes or the local Ollama service. Hugging Face search and downloads require network access; searches send the query and format, while the hardware profile stays on the machine.

Local-first therefore does not mean every workflow is offline. Once a compatible model and runtime are installed, local chat does not require a cloud inference provider.

## Planned, incomplete, or not established

Some repository documentation contains historical roadmap language. Use these implementation distinctions when brainstorming:

- **OpenAI-compatible HTTP API:** planned; no implemented API service is present in the current source tree. Intended future binding is loopback-only (`127.0.0.1`).
- **Terminal chat and standalone migration command:** `chat`, `serve`, and `migrate` exist in CLI argument parsing but currently return “not implemented.” Database migration on application startup does exist.
- **CLI health:** currently reports configuration and a default stopped/unloaded state; it does not inspect the running GUI's live session. CLI model listing is implemented.
- **Prompt templates:** described in the roadmap, without a completed template-management interface in the current app.
- **Packaged installers/bundled runtimes:** future distribution work.
- **Agent tool use, autonomous workflows, document ingestion, cloud-model integrations, collaboration, and sync:** do not assume these exist. They would be proposed extensions.
- **Cross-platform quality:** the code and CI target three operating systems; this does not establish exhaustive real-device testing or identical runtime performance.

## Product direction and constraints to respect

- Keep the experience calm, readable, and easy to navigate.
- Make common actions straightforward; disclose technical details when useful.
- Preserve user control over model choice, memory, and local data.
- Maintain one cross-platform Python/Qt application. Switching frameworks or making the GUI Mac-only would revisit an explicit architectural decision.
- Keep existing conversations and notes usable through changes and migrations.
- Make failures visible and recoverable.
- Prefer useful workflows over adding settings and top-level pages for every capability.

## Useful areas to brainstorm

These are exploration prompts, not an approved roadmap:

1. **A clearer central use case:** Should the strongest story be local AI chat, personal knowledge, project work, or helping people use local models?
2. **First successful session:** How can a new user get from installation to a useful response with fewer confusing choices?
3. **Trustworthy memory:** How should users review, correct, organize, forget, and understand what the app remembers and recalls?
4. **Project depth:** Would project files, scoped knowledge, reusable instructions, or workflow templates make projects more valuable?
5. **Model choice:** How can users choose based on tasks, speed, and quality while keeping estimates honest?
6. **Knowledge reuse:** What would make saved notes actively useful beyond graph browsing and keyword recall?
7. **Integrations:** Which local tools or file formats would benefit most from an API, imports, or exports?
8. **Distribution:** What setup and runtime-management work would make the app practical for less technical users?

## Instructions for an AI using this brief

Help me brainstorm improvements to Orchevian using the current capabilities and constraints above. Clearly distinguish existing behavior from proposed features and assumptions. Do not treat historical roadmap items as completed.

For each strong idea, explain the user problem, give a concrete example workflow, describe how it builds on the app, and identify the main complexity or tradeoff. Suggest a small first version and a way to evaluate whether it is useful. Consider privacy, hardware limits, and interface complexity when relevant.

Challenge weak ideas and help prioritize a few coherent improvements. Ask focused questions when my intended audience or desired direction would materially change your recommendations. Keep brainstorming separate from instructions to modify the code.
