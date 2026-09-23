# Changelog

All notable changes to Orchevian are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Work toward the first public release, 1.0.0. Orchevian is a rebuild of the app
previously called LLM Manager; existing chats, models, projects, and notes carry over.

### Added

- Chat with local models through Ollama, GGUF (llama.cpp), and MLX (Apple Silicon),
  with replies formatted as they stream and **Retry** under the last response.
- Model discovery and downloads from Hugging Face and Ollama, sized to your hardware,
  with a download queue, display names, and storage details.
- Projects with shared guidance and shared files.
- Document attachments with cited sources, image reading, and local OCR.
- File creation at any point in a chat: Word, PDF, Excel, PowerPoint, HTML, charts, and
  data formats, with previews, versions, revisions, and templates.
- Optional web search that works with no setup (Exa's free search, with Bing as a
  backup), follow-up question handling, and optional keys for Exa, Serper, Tavily, and
  Brave Search in **Settings → Web Search**.
- Second Brain: linked Markdown notes captured from chats, with backlinks, a graph
  view, and recall in later chats.
- Templates, Private Chat, an OpenAI-compatible local API, and a terminal chat.
- The model is told today's date in every chat.
- A native-feeling interface with light and dark themes, press feedback, and support
  for the system Reduce Motion setting.
- Model runtimes in separate processes, so a stuck model can be stopped at any time.
- Preview desktop builds for macOS, Windows, and Linux.
