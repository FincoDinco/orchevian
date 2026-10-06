# Changelog

All notable changes to Orchevian are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.1] - 2026-10-06

### Changed

- Second Brain is now called **Memoria**. Existing notes stay where they are.
- Automatic remembering no longer slows chats down. It waits until a chat has been quiet
  for two minutes, or until you move to another chat, instead of running after every
  reply, and a message you send stops it at once and is answered first.
- **Settings → General → Memoria** has both switches, "Automatically remember chats"
  and "Use relevant memories in chats", with what each does. Turn both off to pause
  Memoria; saved notes are kept.
- **Templates** opens on a blank template you can type into straight away; Save creates
  it. You no longer need to click "New template" first.

### Fixed

- Web search failed in the macOS app, with or without a search key: the app's built-in
  security library looked for trusted website certificates where only the build
  machine kept them. Web search now uses the certificate list that ships with the app,
  which also fixes Linux systems that keep certificates outside `/usr/lib/ssl`.
- File creation no longer fails on three small slips smaller models make: a missing
  file name (the title is used), `"chart": "none"`, and a caption on a table (kept as
  the paragraph after it). When creating files does fail, the message lists what was
  wrong in plain terms instead of the validator's raw output.

## [1.0.0] - 2026-10-06

The first public release. Orchevian is a rebuild of the app previously called
LLM Manager; existing chats, models, projects, and notes carry over.
Favourite models pinned in LLM Manager are kept, but Orchevian doesn't show them yet.

### Added

- Chat with local models through Ollama, GGUF (llama.cpp), and MLX (Apple Silicon),
  with replies formatted as they stream and **Retry** under the last response.
- Model discovery and downloads from Hugging Face and Ollama, sized to your hardware,
  with a download queue, display names, and storage details.
- Projects with shared guidance and shared files.
- Document attachments with cited sources, image reading, and local OCR.
- File creation at any point in a chat: Word, PDF, Excel, PowerPoint, HTML, charts, and
  data formats, with previews, versions, revisions, and templates.
- Optional web search that works with no setup (Exa's free search), follow-up question
  handling, and optional keys for Exa, Serper, Tavily, and Brave Search in
  **Settings → Web Search**. Search engines' results pages are never scraped.
- Second Brain: linked Markdown notes captured from chats, with backlinks, a graph
  view, and recall in later chats.
- Templates, Private Chat, an OpenAI-compatible local API, and a terminal chat.
- The model is told today's date in every chat.
- A native-feeling interface with light and dark themes, press feedback, and support
  for the system Reduce Motion setting.
- Model runtimes in separate processes, so a stuck model can be stopped at any time.
- Desktop apps for macOS, Windows, and Linux that run GGUF models (and MLX models on
  Apple Silicon) with nothing else to install.
- The macOS app is signed and notarized by Apple, and its icon follows the macOS 26
  Liquid Glass styles: default, dark, clear, and tinted.

### Security

- The local API requires an API key, limits request size, and blocks browser and
  DNS-rebinding requests.
- Your data folder, database, logs, and notes are private to your account; older installs
  are repaired on startup.
- Search and API keys are stored in the system password vault (Keychain, Credential
  Manager, Secret Service), or an owner-only file where no vault exists. If the vault
  refuses a key, for example while the Keychain is locked, Orchevian says so instead
  of saving the key somewhere else.
- Replies can't inject raw HTML, images, or non-web links into the chat; hovering a link
  shows where it goes.
- Build and test workflows pin third-party actions to exact commits.
- The terminal API server (`orchevian-engine serve`) requires a key you choose, at least
  16 characters, and never prints one.
- API error responses no longer include file paths or a model runtime's own error text;
  Orchevian's log keeps the details.
- Web pages are fetched over TLS 1.2 or newer only.
