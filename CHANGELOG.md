# Changelog

All notable changes to Orchevian are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Work toward the first public release, 1.0.0. Orchevian is a rebuild of the app
previously called LLM Manager; existing chats, models, projects, and notes carry over.
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
