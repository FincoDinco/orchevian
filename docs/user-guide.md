# Orchevian user guide

Everything Orchevian does, feature by feature. For installation, see the
[README](../README.md).

- [Workspace](#workspace)
- [Projects and shared files](#projects-and-shared-files)
- [Attachments](#attachments)
- [Pictures and scanned PDFs](#pictures-and-scanned-pdfs)
- [Models](#models)
- [Web search](#web-search)
- [Creating files](#creating-files)
- [Second Brain](#second-brain)
- [Templates](#templates)
- [Private Chat](#private-chat)
- [Settings](#settings)
- [Stopping a stuck model](#stopping-a-stuck-model)
- [Model runtimes](#model-runtimes)
- [Data locations](#data-locations)
- [Engine CLI](#engine-cli)
- [Local API](#local-api)

## Workspace

Projects and conversations share one sidebar. Chat uses a centered reading area and an integrated composer; open **Chat settings** for chat guidance and response settings. **Models** opens a searchable library with model details and load/unload actions. Ctrl/⌘F focuses search in the current workspace. **View → Hide Sidebar / Show Sidebar** gives the conversation more room (Control-Command-S on Mac; Ctrl+Shift+S elsewhere).

To create a project, use the **+** beside **Projects** or **File → New Project**. The form opens in the center workspace, with the same sidebar and theme. Add a name, optional **Project guidance**, and a model (or **Use app default**). **Get more models…** opens the model browser in the same workspace and preserves your draft; **Back to project** returns to it.

Selecting a project opens its home in the center workspace: a composer, **Files**, its conversations, and a **Project guidance** card. Send from the home composer to start a chat in that project. Each project's unsent home draft and model choice are kept separately during the app session. **Edit project…** edits the name, guidance, and default model in the home; guidance and model changes apply to new chats only. Selecting the project again returns from a conversation to its home.

## Projects and shared files

Use **Files → Upload files…** on a project's home, or drop files into its composer. Once reading finishes, new and existing chats in that project can retrieve passages from those files. Expand **Files** to preview extracted text, save an original copy, replace a file, or remove it. Project files use the same formats and reading limits as chat attachments, with up to 20 files per project. Originals and extracted text persist locally across restarts.

In a project chat, **Project files** beside the prompt selects which files future replies may use. Selections are saved for that chat; newly uploaded files start selected, and replacements keep the selection. Project files and chat attachments share a limit of 8,000 characters of source data per reply. **Sources used** shows excerpts grouped by question, with locations and project-file version numbers.

Replacement becomes available only after successful reading. A failed or cancelled replacement leaves the previous file usable. Replacing or removing a file affects future retrieval; earlier replies keep their source excerpts and version details. Old originals are not retained. Moving a chat switches its project-file access while preserving chat attachments and history. Deleting a project removes its shared files and keeps its chats as unassigned, with their attachments and earlier sources. Deleting a chat keeps the project's files. Private Chat and the local API do not retrieve project files.

## Attachments

In an existing chat, use **+** beside the prompt or drop files into the composer. PDF, DOCX, XLSX, CSV, TSV, UTF-8 text, Markdown, PNG, JPEG, and WebP are supported. Imports run in cancellable background processes. Expand **Attachments** to read extracted text, inspect limitations, remove unsent files, or save an original copy. Originals and extracted text stay in the local database, so saved attachments survive a restart or removal of the source file.

Send a question to include ready attachments in the chat. Replies receive selected passages with page, paragraph, sheet/cell, or row references. **Sources used** shows those passages grouped by question. Each reply uses at most 8,000 characters of source data; extraction is limited to 200,000 characters per file, with 20 files per chat and 20 MB per file. Pictures and selected PDF pages support local OCR and Ollama vision as described below. Word images and headers are omitted, and Excel formulas are displayed without recalculation. Encrypted, corrupt, unsupported, and unreadable files report an error.

Private-chat attachments stay in memory and clear with the private chat. They do not enter the library or Second Brain. The local API does not accept attachments or retrieve saved files.

## Pictures and scanned PDFs

Attach PNG, JPEG, or WebP pictures in chat, or upload them to a project. **Read attachment / Read file** shows a picture or page preview alongside extracted text and its limitations. For PDFs, choose up to eight pages for visual reading and OCR when importing; leave the page field blank for the first eight pages. Ordinary text extraction still covers up to 200 pages. To choose different pages later, replace a project file or import another copy into the chat.

**Use images** beside the attachment controls selects how the next reply reads files. It starts on for pictures and scanned pages, and off for PDFs with readable text. On sends up to four relevant image/page previews with source labels to an **Ollama model marked Vision** in the picker. Capability comes from Ollama's model metadata; the app does not infer it from the name. Unknown or text-only models report the limitation. Off supplies extracted text/OCR only and tells the model it has not seen pictures, charts, or layout. MLX and GGUF currently support this text path only. Existing saved PDFs must be imported again to add image previews.

OCR runs locally inside the disposable parser process, using **Tesseract with English language data**. It makes no network requests and writes no temporary image files. Install the runtime once, then restart the app and import the file again:

- macOS with Homebrew: `brew install tesseract`
- Debian/Ubuntu: `sudo apt install tesseract-ocr libtesseract5 tesseract-ocr-eng`
- Windows: follow [Tesseract's installation instructions](https://tesseract-ocr.github.io/tessdoc/Installation.html). The default `Program Files/Tesseract-OCR` installation is detected.

For nonstandard installations, `ORCHEVIAN_TESSERACT_LIBRARY` can point to the shared library and `TESSDATA_PREFIX` to the language-data folder. Missing OCR does not prevent picture previews or Ollama vision; the file preview shows setup guidance. OCR may misread text, and it does not interpret chart relationships or layout. The first OCR path uses English; handwriting and additional languages are not established capabilities.

Images are limited to 20 MB and 25 megapixels; animated images are rejected. Processing corrects orientation, flattens transparency onto white, removes metadata from model-facing images, and downsizes to a maximum 2,048-pixel edge and 2 MB per preview. The original is preserved. A turn includes at most four previews plus the shared 8,000-character source-data budget; the app reports these limits. PDF rendering and OCR have progress, cancellation, and a 120-second import timeout. Sources retain page/image labels, project versions, and image hashes; replacement/removal discards old image bytes while retaining prior source records.

The image switch persists per regular chat; private images, OCR text, previews, and the switch clear with the private session. Project images follow project-file selection and retention rules. The local API remains text-only. See [Ollama's image API](https://docs.ollama.com/capabilities/vision) for its underlying image-message format.

## Models

> **Models have their own licenses.** Orchevian doesn't include any models. Each model you
> download comes with its own license, and some (for example Llama and Gemma) restrict
> certain uses. Check a model's license on its Hugging Face or Ollama page before relying
> on it, especially for commercial work.

**Settings → Models → Default model for new chats** chooses the app-wide fallback. An explicit model choice wins, then a project default, then the app default. Existing conversations keep their own model. Defaults retain the complete Ollama, GGUF, or MLX reference; a missing model or unavailable runtime is shown without silently substituting another model. Choosing a default does not load it.

Models use short display names, such as **Qwen 3 · 8B**, grouped under Ollama, GGUF, and MLX. Select a downloaded model and choose **Edit model…** to edit its display name in place. Save commits the name; Cancel discards the edit; a blank name restores the suggestion. Names are saved locally and appear in chats and project selection too. Renaming does not move model files or change existing conversation references. Original identifiers remain in the editor, tooltips, and model details.

In **Models → Downloaded**, select **Delete model…** to remove a model from disk (or from Ollama). Deletion asks for confirmation, unloads the selected model first, and keeps conversations. Stop any active generation before deleting.

**Models → Downloaded → Storage** expands a summary of model counts and reported sizes by backend, free space on the model-folder drive, and **Reveal model folder**. It refreshes with the model catalog after downloads or deletions. These are catalog sizes rather than unique disk usage: Ollama tags can share files, and Ollama manages its own storage location. Unavailable backends show unknown sizes when no local catalog is available. If the configured model folder does not exist yet, free space is measured at its nearest existing parent without creating folders. A disk-space error leaves the model totals visible.

**Models → Get more models** opens with suggestions estimated to fit your computer. Choose **Format** (Automatic, MLX, GGUF, or Ollama) beside **Quantization**, then search by name. For MLX, bit depth filters repositories by advertised precision; for GGUF, select a model and choose **Choose download** to see available files, bit depths, and sizes. Select **Download** to install it, then **Downloads → Open model** to load it and start chatting. Turn off **Suggested for my computer** to search beyond the estimated memory budget.

The **Downloads** button in the top-right toolbar opens a compact panel over your current workspace. Its count shows unfinished downloads. Expand a row to see the repository, version, file count, and size; cancel, retry, and open actions stay within reach. Escape or a click outside closes the panel while downloads continue. Up to three models download at once; additional downloads wait in the queue. Queued entries display their status without a progress bar; cancellation shows activity while partial files are removed.

The sidebar holds New chat, search, its collapse toggle, and a **+** beside **Projects**. The downloads toolbar sits inside the main workspace so it does not interrupt the sidebar. The standard native titlebar and window frame remain available for dragging, resizing, minimizing, and maximizing. In Downloaded, **Model Details** expands technical information and model management actions; the model name and primary chat action stay visible.

**Options…** holds an optional memory limit, hardware details, and the Hugging Face read token. Automatic format and memory settings are the default. **Model details** shows the original repository name, technical estimates, and the model-page link. Available download versions remain visible once files are checked; the default prefers Q4 GGUF files.

**Ollama** downloads use a `model:tag`: enter one and choose **Choose tag → Download with Ollama**. **Browse Ollama tags** opens the library to find the model size and quantization you want. Ollama must be running. Its downloads share the queue, progress, cancellation, and retry controls; size is reported during the pull, storage is managed by Ollama, and cancelled pulls can resume.

Hugging Face downloads show file sizes before starting, pin files to one repository revision, check disk space, and verify file lengths and published SHA-256 checksums when available. GGUF offers individual quantizations, including complete split-file sets; MLX installs weights, configuration, and tokenizer assets. Downloads run in the background with progress and cancellation. Partial files stay hidden from Downloaded and are removed on cancellation or failure; successful downloads refresh the library automatically. Split GGUF files appear as one model and are deleted together. Cancelled downloads restart from the beginning when retried.

For gated models, accept access on the Hugging Face model page and enter a read token in **Options…**. Tokens entered there are kept only for the current app session; `HF_TOKEN` is also supported. The model-page link remains available for manual downloads. Downloading does not load or execute a model.

Recommendations run on **macOS, Windows, and Linux**, using [psutil](https://psutil.readthedocs.io/) for total and currently available RAM. **Auto format** chooses MLX on Apple Silicon and GGUF elsewhere, including Intel Macs and CPU-only systems. NVIDIA memory is probed with `nvidia-smi` on Windows/Linux; AMD VRAM is read from Linux DRM when available. Unrecognized GPUs (including AMD/Intel on Windows) fall back to system-RAM recommendations. Detecting a GPU does not establish that the installed runtime supports it. Results distinguish GPU-memory fit from CPU-memory fit; CPU inference may be slow.

Apple unified memory is counted once, and separate GPUs are evaluated individually without summing their VRAM. Busy machines receive a smaller budget. If detection fails, search still works and **Options… → Memory limit** lets users supply a limit; otherwise that control can reduce the detected budget. Hardware information stays on the machine; Hugging Face receives the search query and format, not the hardware profile. Search uses the [public Hugging Face API](https://huggingface.co/docs/hub/api).

Place downloaded GGUF files under `model_dir/gguf/`; put complete MLX repositories (weights, configuration, and tokenizer) under `model_dir/mlx/`. Nested owner/repository folders are supported. Refresh Downloaded after downloading. Local files remain visible and deletable when an optional runtime is missing; loading is disabled with setup instructions.

## Web search

Turn on **Web search** below the message field to search before the local model
answers. It starts off. The choice stays with each chat or project-home draft;
starting a project chat carries the choice over. **Retry** under the last message
uses the same choice and reads **Retry with web search** when it is on. New private
sessions start off.

Search builds a focused query from the first 500 characters of the current message
(Exa receives the question itself), then uses up to three relevant pages. Conversational filler is
removed; simple recent Fed-rate questions become dated Federal Reserve decision
searches without assuming rates went up or down. Explicit dates and comparisons
are preserved; "9/22" becomes "September 22", and "today" or "yesterday" adds the
calendar date. A follow-up with no subject of its own, such as "Specifically today
9/22", borrows the subject of one of your previous three questions in that chat. It
does not send attachments, project files, guidance, memories, or other earlier messages.
Every chat also tells the model today's date, so "today" means the same thing to it as
it does to you. Inference stays local. The button's tooltip explains
what is sent. Search-off makes no search or page-reading requests.

By default search uses [Exa's free keyless search](https://exa.ai/docs/reference/exa-mcp),
which returns page highlights directly, so it also covers pages that need JavaScript, such
as weather forecasts. No API, account, key, subscription, shared developer credential,
or hosted Orchevian backend is required. If Exa is busy or at its free limit, the reply
stops with an explanation; try again later or add a free key. Orchevian never scrapes
search engines' results pages, which their terms and robots.txt forbid.

**Privacy:** the search service receives your question (up to 500 characters) and may keep
it under its own privacy policy; see [Exa's terms](https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf)
or those of the service you added a key for.

**Optional: a bigger allowance with your own free key.** Exa's free search has a daily
limit. Heavy users can open **Settings → Web Search**, which explains each step: pick a
service (Exa is the easiest start: free monthly credits, no credit card), click **Get a free
key**, create an account, copy the key, paste it in and click **Test**. Serper also has a free
start without a card; Tavily and Brave Search need a card on file. With several keys,
Orchevian tries them top to bottom, then Exa's free search; **Web sources** says which service answered and why any was skipped. Your key
stays in Orchevian's settings on this computer and is only sent to that service.

Search and reading progress appear above the composer. **Stop** cancels the reader
process, including blocked network/DNS calls. The whole retrieval has a 45-second
deadline, each network operation an 8-second timeout, and each page a 3 MB limit.
Connecting to a site shares one 8-second budget across all of its addresses, and
further results from a site that timed out are skipped.
Only public HTTP(S) HTML/text pages are read; redirects and resolved addresses are
validated and connections use the validated IP address. No scripts, cookies,
login sessions, proxy environment variables, PDFs, or browser automation are used.

**Web sources** shows each searched question, provider, retrieval time, linked
titles/URLs, the actual successful search query, and excerpts supplied to the model
(up to 8,000 characters of
source context). Regular source history survives restart, with the newest 20
search records displayed. **Retry** replaces that question's search record.
Private sources stay in memory and clear with the private chat. Search failure or
empty results stops that reply with a visible error; unreadable individual pages
are disclosed when other pages succeed. Up to ten candidates are considered;
result titles/URLs must match the subject, extracted passages must match at least
half of the query's topic terms, and every named subject in your question (for example
"FED" or "Donald Trump") must appear on the page. This lexical check rejects unrelated pages rather
than filling a three-page quota. If none qualify, the reply reports that it found
no sufficiently relevant pages. These checks do not establish factual correctness,
semantic relevance, or freshness. Source relevance and model citation
accuracy still require judgment.

## Creating files

Turn on **Create files** beside the prompt in chat or project home, then describe the deliverables and desired formats. For example: “Use the attached brief to create a DOCX proposal and a PPTX briefing with speaker notes.” Turn it off for ordinary chat replies. The choice follows each draft, and **Retry** uses the current choice. You can create files at any point in a chat.

The model returns a validated `create_documents` specification. The app permits one correction attempt and runs native generators in a cancellable child process. It never runs model-written Python, shell commands, macros, or HTML scripts. Progress appears above the prompt; invalid output produces a recoverable error without publishing a partial batch. The normal response-token setting also bounds creation; complex files may need a higher **Max tokens** setting or smaller requests.

| Output | Implemented content |
| --- | --- |
| DOCX | Headings, paragraphs, lists, tables, page breaks, printable form blanks |
| PDF | Paginated reports, slide handouts, charts/process diagrams, fillable text and checkbox forms |
| XLSX | Multiple sheets, typed cells, number formats, formulas, bar/line charts |
| PPTX | Editable titles and bullet text, slide numbers, speaker notes |
| HTML | Standalone styled reports, escaped content, no external assets or scripts |
| JSON, YAML, XML | Validated structured data; XML uses a typed `data`/`entry`/`item` tree |
| SVG, PNG | Bar/line charts and ordered process diagrams |
| Markdown, TXT, RTF | Text exports; Markdown preserves headings/lists/tables, RTF is plain text |
| CSV, TSV | Single-sheet values; formula-like text is escaped |

Open **Generated files** for versioned file cards. **Preview** shows rendered pages and retained content/source references. **Save As** exports explicitly; **Open** first saves your chosen copy, then opens its associated application. **Save batch ZIP** exports the latest related multi-file request. **Revise** selects that version and starts an editable follow-up prompt. Keep the filename to create a new version; request another supported format to convert the retained specification. Original versions remain available after restarting.

**More → Add to project** stores a copy and its retained content for later project retrieval, including formats the upload readers cannot import. **More → Save as reusable template** stores the specification and its content; choose it later from **File templates**. Templates are separate from the sidebar's prompt templates. Creating a format does not imply importing or preserving arbitrary existing files in that format.

PDF/image previews render the generated output. Office previews show content rendered by the app, **not the native Word/Excel/PowerPoint layout**; open those files for final layout inspection. Previews show up to eight pages; spreadsheet previews show the first 40 rows and eight columns. Formulas support cell arithmetic and `SUM`, `AVERAGE`, `MIN`, `MAX`, `COUNT`, `COUNTA`, `IF`, `ROUND`, and `ABS`. Syntax/reference checks do not calculate values: Excel or LibreOffice must recalculate. No cached formula results are claimed.

Limits: eight files/request, 120,000 characters of model output, 20 MB/output file, 80 versions and 100 MB of files/previews per chat, 90 seconds per generator batch, and 40,000 characters of retained revision/template context. A model that cannot emit the required structured output receives an explicit error after two attempts. Context comes from the same bounded attachment/project retrieval as chat, with source snapshots retained for each generated version. Private generated files and previews stay in memory and clear with Private Chat; explicit Save As can export them, while saved-template and Add to project actions are disabled there. The stateless API and background memory extraction do not invoke creation tools.

## Second Brain

**Settings → General → Automatically remember chats** is on by default. After a completed regular response, the local model selects durable facts, preferences, decisions, and useful ideas, filters out unhelpful or repeated material, and adds connected notes to Second Brain. Notes require exact conversation evidence and remain editable. Capture runs when the model is idle and briefly uses the same model session; the activity pill at the top of the workspace shows its progress and **Stop** ends it. Turning the setting off cancels automatic capture and leaves existing notes intact. It applies to new completed responses, without scanning old chats on startup.

**Second Brain** stores editable Markdown notes with `[[wiki links]]`, backlinks, search, and an interactive graph. Create notes yourself or select **Remember chat** to extract up to six memories using the conversation's local model. Generated notes include an evidence quote, model attribution, and a linked snapshot of the conversation. Repeating capture on an already saved conversation version reuses its memories.

Notes live in `second-brain/` beside your database by default. **Choose vault…** opens another folder, including an existing Markdown vault. Notes can be edited outside the app; select **Refresh notes** to pick up changes. Saving reports a conflict when a note has changed on disk. Deleted notes move to the vault's `.trash/` folder.

**Use relevant memories in chats** is enabled by default. It adds up to four notes matched by keywords plus up to six notes tagged `about-me`, excluding source transcripts and duplicates. Personal notes can help even when your question uses different words. Generated personal notes must cite your own messages; assistant statements alone cannot add them to your profile. Evidence matching tolerates whitespace and Markdown list markers. You can add or remove the `about-me` tag in a note, or turn recall off in Second Brain to chat without recalled notes. Memory capture runs in the background and can be cancelled.

## Templates

**Templates** in the sidebar (Ctrl/⌘5) stores reusable chat guidance and starter messages. Create a template with a name, optional description, guidance, and a starter message; use search to find it by name or description. **Save** keeps changes locally. **Use in new chat** saves any edits, creates an unassigned conversation using the app's default model, and places the starter message in the composer for editing. It does not send or load a model. Choose a model if no app default is set.

Templates copy their guidance into a new chat. Editing or deleting a template leaves existing chats intact. Unsent regular chat drafts are kept separately per conversation during the app session, including when using a template. Template edits stay in the editor when navigating to another workspace; switching templates, creating another template, or quitting offers Save, Discard, or Cancel for unsaved changes. Template navigation is disabled in Private Chat.

## Private Chat

**Private Chat** opens from the top-right toolbar beside Downloads (Ctrl/⌘Shift+P also opens it). It fills the app workspace, hides the sidebar and toolbar, and stays active until you select **Clear private chat**. Navigation shortcuts are disabled during private chat. Only messages and explicitly attached documents from the current private chat provide context: saved conversations, project guidance, and Second Brain are excluded. Private messages never enter the conversation database or memory vault. Clearing the chat or closing the app stops the response and erases its transcript and unsent draft. Clearing returns to your regular workspace and restores your regular draft; reopening private chat starts empty.

## Settings

**Settings** stays inside the main workspace. Open it from the sidebar or Ctrl/⌘, and use **Back to chats** or the sidebar to return.

## Stopping a stuck model

Select **Cancel loading** in the model library, or use **Stop** in the activity pill at the top of the workspace, which shows what the model is doing and the elapsed time while it works. It remains available when you switch workspaces. **Escape** stops the current load, chat response, or memory capture; **Ctrl/⌘Shift+.** force-stops the active model operation.

Model runtimes run in separate worker processes so a blocked native loader can be terminated without waiting for it to return or closing the app. Loads and waits for the first response time out after 120 seconds; a stream that stalls after starting is stopped after 60 seconds without a token. MLX requests direct answers when the model template supports disabling thinking. Waiting for a response is shown separately from explicit model thinking. Stopping preserves your conversation and any partial response; you can then choose a smaller model or retry. A stopped runtime is loaded again for the next request.

For Ollama, stopping also asks its service to release the model using [`keep_alive: 0`](https://docs.ollama.com/api/generate). If the service does not confirm unloading within five seconds, the app reports the failure and shows a recovery command. An unresponsive external Ollama service may need `ollama stop MODEL_NAME` or an Ollama restart.

## Model runtimes

| Backend | Availability |
| --- | --- |
| Ollama | First-class, all OSes |
| GGUF | Optional extra `gguf` (`llama-cpp-python`), all OSes |
| MLX | Optional extra `mlx` (`mlx-lm`), Darwin/arm64 only |

A missing extra is shown as unavailable. It does not crash the app.

On Apple Silicon, keep both extras installed with `uv sync --extra gui --extra mlx --extra gguf`. Running `uv sync` without the extras removes their optional packages. GGUF uses full GPU offload when the installed llama.cpp build supports it and estimated model memory fits the detected GPU budget; otherwise it uses CPU. MLX requires a macOS session with access to Metal. The MLX extra is platform-gated, so it is skipped on Windows, Linux, and Intel Macs.

## Data locations

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

Subcommands: `chat`, `models`, `serve`, `migrate`, `health`. Global `--config`, `--db`, and `--verbose` options work before or after the subcommand.

```bash
uv run orchevian-engine migrate                         # migrate DB and report schema/backup
uv run orchevian-engine chat --model ollama/qwen3:8b      # saved terminal conversation
uv run orchevian-engine chat --conversation 12           # resume a saved conversation
uv run orchevian-engine serve --port 8080                # standalone local API
```

Terminal chat streams responses into the terminal and saves user and assistant turns. Use `/exit`, `/quit`, or EOF to leave; Ctrl+C during a response stops it and preserves partial output. With no `--model`, a resumed chat keeps its model, or a new chat uses the sole available model; otherwise the command asks you to specify a model ID from `models`. `--max-tokens` sets the response limit. Terminal chat does not automatically capture or recall Second Brain notes. Prefer closing the GUI before running saved terminal chats against the same database.

`migrate` uses the same additive migrations and pre-engine backup as GUI startup. Repeating it is safe. `health` reports configuration and explicitly says it does not inspect another process's live model session.

## Local API

Open **Settings → API → Enable local API** to connect a local client to the GUI's model session. The tab provides a port setting, read-only base URL, **Copy URL**, the **API key** with **Copy Key** and **Regenerate**, terminal examples, and the last 50 request summaries. Summaries contain the method, path, HTTP status, and time to response headers; they stay in memory and omit message content. The API starts disabled on each app launch. Disabling it or quitting the app stops its requests and closes the listener.

The API binds only to `127.0.0.1` and exposes:

- `GET /v1/models`: available model IDs in `backend/name` form.
- `POST /v1/chat/completions`: text chat, with regular JSON or streaming server-sent events.

Every request must include the API key, the same way OpenAI clients send theirs. In most
apps, paste it into the "API key" field.

```bash
curl http://127.0.0.1:8080/v1/models -H 'Authorization: Bearer YOUR_API_KEY'
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Authorization: Bearer YOUR_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"model":"ollama/qwen3:8b","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

Replace the model with an ID from `/v1/models`. Supported request fields are `model`, `messages` (text `system`, `user`, or `assistant` messages), `stream`, `temperature`, `top_p`, `max_tokens`, and `n` (1 only). Unsupported fields, tools, and multimodal content return an error. Request bodies are limited to 4 MB and `max_tokens` to 32,768. Token usage is omitted because the backends do not consistently report token counts. Streaming responses use assistant/content deltas and a final `[DONE]`; errors after streaming starts appear as an error event instead of a successful finish.

An idle session automatically loads the requested model. API requests share the GUI's one-operation limit with regular/private chat, model management, and memory capture; a competing request returns HTTP 429. Disconnecting a client or stopping the API cancels that API request. It does not stop an unrelated GUI request. API prompts and replies never enter saved conversations or Second Brain, and API requests receive only the messages supplied by the client.

`orchevian-engine serve` runs the same API with its own model session and an in-memory library; it does not open or migrate your conversation database. Use the GUI's API when you want clients to share the GUI's loaded model. Only one server can use a given port. It prints a new API key at startup, or uses the `ORCHEVIAN_API_KEY` environment variable when set. Both servers require the key, accept only `127.0.0.1`/`localhost` host names (blocking DNS-rebinding attacks), and reject browser-origin requests; neither enables CORS.
