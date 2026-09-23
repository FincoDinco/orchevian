# Orchevian — Project Context for AI Brainstorming

Snapshot: September 22, 2026. Based on the current working tree, including work that may not yet be committed or released. This document is self-contained so it can be shared with another AI without sharing the repository.

## What this project is

**Orchevian is a local-first desktop AI workspace for discovering, downloading, running, and chatting with language models on your own computer.** It also organizes conversations into projects and turns useful information from chats into a personal, editable Markdown knowledge base called **Second Brain**.

This snapshot includes app-wide default models, in-shell project creation/home, and inline display-name editing in the working tree.

Latest continuation (September 22, afternoon): broader real-model acceptance.
`scripts/check_model_matrix.py` runs nine isolated cases with the installed
`avan-ag/Qwen3.5-4B-Uncensored-MLX-4bit` model: spreadsheet inventory creation/
revision (arithmetic formulas, currency formatting), slide revision, source-grounded
writing with missing facts and embedded instructions, four synthetic web-evidence
cases (dates, unknown answer, false premise, prompt injection) and two live
searches (Artemis I launch, Python 3.13.0 release). Fixes found by the matrix:
the formula validator now accepts ordinary arithmetic and routes circular references
to repair; search keeps exact subjects/versions and avoids leading "date" terms;
retrieval timestamps are no longer shown to the model; the checker requires the
answer's evidence in the cited page and accepts abbreviated months. A later rerun
found the live Python case timing out because `www.python.org`/`docs.python.org`
were unreachable from the development network (even with curl) and each unreachable
host spent 8 seconds per resolved address (32 seconds). Connections now share one
8-second budget across addresses, and further results from a host that timed out
are skipped; the same search then finished in 18 seconds from `blog.python.org`.
`dist/model-matrix-08/` passed all nine cases. These bounded cases are not a
general quality guarantee. Validation: 651 tests passed, Ruff and `git diff --check`
passed, and the rebuilt macOS arm64 preview passed all ten frozen smoke checks,
both built and after checksum-verified extraction into a path containing spaces
with Python environment overrides removed. Native Office layout/recalculation,
clean-machine checks, and release packaging remain open.

Earlier September 22 continuation: recovered the completed revision checks below
and rebuilt the macOS arm64 desktop preview with those fixes. All ten frozen
smoke checks passed, covering temporary-library migrations, spawned fake inference,
the local API, document reading/creation, image handling, project/web sources,
exports, and Qt startup/shutdown. A separate archive check verified SHA-256,
extracted into a temporary path containing spaces, removed Python environment
overrides, and passed all ten checks again. Reports are
`dist/smoke-report.json` and `dist/archive-smoke-report.json`; the refreshed preview
is `dist/Orchevian-0.1.0-darwin-arm64.tar.gz`. This is validation on the development
Mac, not a clean-machine or other-platform pass. No native Office access was
retried. Signing/notarization, installers, runtime bundles, native Office layout/
recalculation, and clean-machine real-model validation remain open.
Validation: all 601 regression tests passed with loopback access, plus Ruff and
`git diff --check`. The sandboxed attempt failed ten loopback-dependent tests;
the complete rerun outside the sandbox passed. Existing Qt signal-disconnect and
Starlette deprecation warnings remain. Only documentation and local preview
artifacts changed in this continuation; the revision implementation was already
complete in the recovered working tree.

September 21 continuation: two consecutive full artifact fixtures passed with the
installed Qwen 3.5 4B MLX model. The earlier revision failure was a complete file
array missing only the final outer `}`. The parser now completes that envelope
and runs full schema/content validation; it never completes truncated file content
and rejects duplicate JSON keys. Explicit revisions must return exactly one file
under the selected filename. Missing-source repairs supply concrete supported
structures and collect errors across the batch, retaining the single retry limit.

Runs `dist/artifact-acceptance-13/` and `dist/artifact-acceptance-14/` passed primary
creation, the 40-to-50-participant proposal revision with budget and source
attribution retained, original preservation, extended PDF/HTML/JSON/SVG creation,
ZIP export, project reuse and library reopen. PDF field names are explicit in
the fixture prompt. The checker reads native content and checks PDF form values
after filling/reopening. These bounded fixture passes supersede the earlier failed
result; they are not a guarantee for arbitrary requests/models. Local reports and
previews are ignored by Git. No models were downloaded or saved chats opened.

Native Word inspection was attempted, but automatic approval review rejected the
Open action because Computer Use was not approved for Word. After the user asked
why Word access was needed, the assistant explained that it is optional for the
revision fix and committed not to retry access. Native Office layout/recalculation
and clean-machine checks remain open. Do not claim those checks passed.
Validation: 600 full-suite tests passed with loopback access; final attribution
repair changes and regression coverage passed 78 focused tests. Ruff and
`git diff --check` passed. Existing Qt and Starlette warnings remain.

September 18 web continuation: live web retrieval plus an existing MLX model now has a
repeatable temporary-library check in `scripts/check_web_answer.py`. The installed
`avan-ag/Qwen3.5-4B-Uncensored-MLX-4bit` model answered the reported Fed-rate
question with an event date and retrieved-source citation after preserving short
source datelines and clarifying the response format; a following search-off turn
performed no retrieval. `--expect-date` can now check the visible answer without
supplying the expected date to the model. Broader answer quality, real-model
artifact creation, native Office layout, and clean-machine distribution remain
open. No models were downloaded. Model-picker tests now use isolated settings.
Validation on September 18: all 555 tests passed outside the sandbox (loopback
listeners require that access), Ruff passed, and `git diff --check` passed.
The suite still emits Qt signal-disconnect and Starlette deprecation warnings.
The subsequent date changes passed 61 focused tests and three desktop smoke/
packaging tests, plus Ruff and the whitespace check; the full suite was not rerun.
The existing MLX model also passed synthetic-source checks for an event predating
publication and an unknown event date, retaining a citation in both answers.

The project was previously called **LLM Manager**. Orchevian is the current product name; the repository and Python package names still contain the older name for compatibility.

The practical idea is to bring model management, everyday AI conversations, and personal knowledge together in one calm desktop interface. Users can choose their own local models, retain their conversations and notes locally, and reuse relevant knowledge in later chats.

This is an actively developed personal project owned by Seth Hardin. The package version is currently `0.1.0`. It is a working desktop application launched from a Python development environment. Initial preview packaging bundles Python and Qt with a separately installed Ollama service; polished installers and native inference runtime bundles remain future work.

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
- Provides a searchable downloaded-model library with load, unload, reveal-on-disk, and confirmed deletion actions. Its Storage disclosure shows catalog model sizes/counts by backend and free space on the configured model-folder drive, distinguishing unavailable backends and disk-space failures. Catalog sizes can double-count shared files, particularly Ollama blobs.
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
- Includes conversation search, rename, deletion, and moving chats between projects. Unsent regular-chat drafts are kept separately per conversation for the app session.
- Offers light and dark themes, keyboard shortcuts, a hideable sidebar, and settings inside the main workspace.
- Reads chat attachments (PDF, DOCX, XLSX, CSV/TSV, UTF-8 text, Markdown) through cancellable parser processes. The composer supports file selection and drop; users can preview extracted text, remove unsent files, and save originals. Regular originals and extracted text persist in SQLite. Replies use up to 8,000 characters of source data with locations and a source preview grouped by question. Imports are capped at 20 MB/file, 20 files/chat, and 200,000 extracted characters/file. Pictures and selected scanned-PDF pages support local English OCR through separately installed Tesseract, plus image interpretation through compatible Ollama models. A per-chat Use images switch distinguishes vision from extracted text. Import supports PNG/JPEG/WebP, up to eight selected PDF pages, 25 megapixels per picture, and four normalized previews per reply. Private visual data stays in memory. Office text extraction has visible limitations and does not recalculate formulas.

### 3. Projects

A project groups related chats and supplies a default model and guidance for new conversations. For example, a user can create a writing project with a preferred model and instructions about tone.

Project creation takes place inside the main workspace. Users can choose a downloaded model or inherit the app default, browse models, and return without losing the creation draft. Selecting a project opens an in-shell home with its own composer, conversation list, and editable guidance card. Home drafts and model choices are kept separately per project during the app session. Submitting creates a regular project chat. Editing project defaults affects new chats; existing conversations retain their settings. A Files area supports shared uploads, cancellable reading, preview, saving originals, replacement, and removal. New and existing project chats can retrieve selected files on future turns; per-chat selections persist. Project files and chat attachments share the bounded source-context budget. Replies retain versioned excerpts when files change or are removed. Moving a chat switches future file retrieval while preserving its attachments and history. Deleting a project removes its shared files and keeps its chats as unassigned with their attachments and prior source records.

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
- Only the current private conversation and its explicitly attached documents supply its chat context. Private originals, extracted text, and latest-reply source previews stay in memory and clear with the chat.
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

Conversations, project data, preferences, and notes are stored locally. Inference uses local model runtimes or the local Ollama service. Hugging Face search and downloads require network access; searches send the query and format, while the hardware profile stays on the machine. Optional chat web search sends a focused query derived from the current question (up to 500 characters) to Bing and fetches public result pages; it starts off and requires no API key or account.

Local-first therefore does not mean every workflow is offline. Once a compatible model and runtime are installed, local chat does not require a cloud inference provider.

## Planned, incomplete, or not established

Some repository documentation contains historical roadmap language. Use these implementation distinctions when brainstorming:

- **OpenAI-compatible HTTP API:** implemented for text model listing and streaming/non-streaming chat on `127.0.0.1`. Settings → API controls the GUI server, sharing its single model session. API messages are stateless and exclude saved chats and Second Brain. Last 50 request summaries stay in memory. Tools and multimodal requests are unsupported.
- **Terminal chat and standalone migration command:** implemented. Terminal chat streams, saves, resumes, and cancels responses; it does not use Second Brain. `migrate` uses the existing additive startup migration path. `serve` runs the API with its own model session and an in-memory library, without opening the conversation database.
- **CLI health:** reports configuration and explicitly says that live session state is not inspected; it does not report a fabricated stopped/unloaded state. CLI model listing is implemented.
- **Prompt templates:** implemented in the sidebar, with search, creation, editing, deletion, and “Use in new chat.” Templates store guidance and a starter message in the existing local database. Using one saves edits and opens an unassigned chat with the app-default model and an editable unsent draft. Existing chats retain their copied guidance after template edits/deletion. Template edits are protected by Save/Discard/Cancel when replacing the editor or quitting.
- **Desktop packaging:** initial PyInstaller build recipe, frozen-app smoke check, platform archives/checksums, and a macOS/Windows/Linux artifact workflow are implemented. Preview bundles include Python and Qt; Ollama is installed separately, and MLX/GGUF runtimes are excluded. Signed releases, installers, native inference bundles, and clean-machine validation remain future distribution work.
- **Documents, project files, and artifact creation:** chat uploads, text reading, shared project files, picture/scanned-PDF OCR and Ollama vision are implemented. An explicit **Create files** composer choice now creates DOCX, PDF (including fillable text/checkbox forms), XLSX, PPTX, standalone HTML, Markdown/TXT/RTF, CSV/TSV, JSON/YAML/XML, and SVG/PNG charts or ordered process diagrams. Creation uses strict specifications and isolated generators; the model gets one correction attempt. No model-written programs execute. File cards support previews, explicit save/open, batch ZIP, retained versions and chat revisions, reusable specification templates, and project copies with retained reference text. Private files/previews remain in memory unless explicitly exported; they cannot enter saved templates or projects. File creation and import capabilities are separate. Office previews render content rather than native Office layout, and formulas require Excel/LibreOffice recalculation. Real-model answer/creation quality, native Office layout, and clean-machine validation remain acceptance gaps. Ollama listed no installed models during the September 17 check. Optional web search now has an initial implementation; real-model sourced-answer acceptance remains open. Distribution completion follows; see `DESIGN.md` for detailed scope and acceptance criteria.
- **Tool use:** a bounded `create_documents` JSON tool flow is implemented for the artifact creator above. General autonomous workflows, cloud-model integrations, collaboration, and sync remain unimplemented and are not established product commitments.
- **Chat web search:** initially implemented in the working tree. An off-by-default **Web search** button in chat/project home retains per-draft state and transfers into new project chats. **Retry** under the last message reuses that choice and reads **Retry with web search** when it is on. Bing public HTML search (web and news) requires no API, account, key, subscription or hosted Orchevian backend, per the September 17 open-source distribution requirement. By default search uses Exa's free keyless search (its hosted MCP endpoint, one stateless call; Exa documents free rate-limited use without a key), and Bing when Exa is busy or at its limit. Optionally, each person can add their own key for Exa, Serper, Tavily or Brave Search in Settings → Web Search (Tavily and Brave need a card on file); keys are tried in that order, then free Exa, then Bing. Keys are the user's own (no shared developer credential), are stored in the app's local settings file, and are sent only to that service. Only a focused query built from the current message (capped at 500 characters) goes to search. A follow-up with no subject of its own (such as "Specifically today 9/22") borrows the subject of one of the three previous questions in that chat. Numeric dates are spelled out and today/yesterday gain the calendar date. Every chat's system prompt starts with the local calendar date so the model reads 'today' and dated sources correctly; the stateless local API is unchanged. DuckDuckGo was retested on September 22 and still answers automated requests with its bot challenge after the first search, so Bing remains the only provider. Conversational filler is removed; simple recent Fed-rate questions become neutral dated decision searches. Up to eight candidates are checked for subject matches and topical excerpt coverage before retaining at most three sources; unrelated pages are discarded, and no qualifying sources produces an explicit error. The actual successful query is displayed. These lexical checks do not guarantee semantic relevance or freshness. An isolated reader fetches public HTML/text pages with DNS/IP/redirect enforcement, progress, cancellation, a 45-second deadline, three readable sources, and 8,000 characters of source context. Linked titles, URLs, timestamps and exact excerpts persist per question; regeneration replaces that record, and private evidence remains temporary. Provider errors and empty results stop the reply visibly. Off means zero retrieval. Memory capture and stateless API requests do not inherit the toggle. Deterministic tests and an offline packaging fixture cover the implementation. Bing was selected after the initial DuckDuckGo request was blocked. A live regression check of the reported Fed-rate question retrieved rate-decision articles; deterministic tests reject Cleveland Clinic/dictionary results and unrelated material for another topic. A live check is available in `scripts/check_web_search.py`; real-model sourced-answer quality and clean-machine provider availability remain acceptance gaps. This is public page reading, not browser automation.
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
