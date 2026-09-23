# Orchevian

**A private, local-first AI workspace for your desktop.** Run language models on your
own computer, chat with your files, create documents, search the web when you choose
to, and keep a personal knowledge base. Your conversations stay on your machine.

![Orchevian chat in dark mode](docs/screenshots/chat-dark.png)

## Features

- **Chat with local models.** Use models from [Ollama](https://ollama.com), or run
  GGUF (llama.cpp) and MLX (Apple Silicon) models directly. Replies are formatted as
  they stream in.
- **Find and download models.** Browse Hugging Face and Ollama from inside the app,
  with suggestions sized to fit your computer's memory.
- **Projects.** Group related chats, give them shared guidance, and upload files that
  every chat in the project can draw on.
- **Chat with your documents.** Attach PDF, Word, Excel, CSV, text, Markdown, and
  images. Replies cite the passages they used. Scanned pages can be read with local
  OCR once [Tesseract](https://tesseract-ocr.github.io/) is installed.
- **Create files.** Ask for a Word document, spreadsheet, slide deck, PDF, chart, or
  data file at any point in a chat, then preview, revise, and save it.
- **Optional web search.** Turn it on for a single question. It works with no setup
  and no account; you can add your own free search key for a bigger allowance.
- **Second Brain.** Useful facts from your chats become linked, editable Markdown
  notes with an interactive graph, and relevant notes are recalled in later chats.
- **Private Chat.** A session that is never saved, never remembered, and cleared
  when you close it.
- **Templates, a local API, and a command line.** Reusable starting prompts, an
  OpenAI-compatible API on `127.0.0.1`, and a terminal chat.

<p align="center">
  <img src="docs/screenshots/models.png" width="49%" alt="Model library">
  <img src="docs/screenshots/project.png" width="49%" alt="Project home">
</p>

## Install

Orchevian is in development ahead of its 1.0 release. For now, run it from source:

1. Install [Python 3.13](https://www.python.org/downloads/) and
   [uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Install at least one model runtime. [Ollama](https://ollama.com) is the simplest
   and works on macOS, Windows, and Linux.
3. Download and start Orchevian:

   ```bash
   git clone https://github.com/FincoDinco/llm-manager.git orchevian
   cd orchevian
   uv sync --extra gui
   uv run orchevian
   ```

   To run GGUF or MLX models without Ollama, add `--extra gguf` (all systems) or
   `--extra mlx` (Apple Silicon) to `uv sync`.

4. Open **Models** to download a model, then start a chat.

Preview desktop builds that don't need Python are described in
[packaging/README.md](packaging/README.md). They are not yet signed or packaged as
installers.

## Privacy

Models run on your computer, and your chats, files, and notes are stored locally.
Orchevian connects to the internet only when you ask it to:

- **Web search**, when you turn it on for a message. Up to 500 characters of that
  question go to the search service (Exa's free search by default, Bing as a backup,
  or a service you added a key for). Files and saved chats are never sent.
- **Model browsing and downloads** from Hugging Face or Ollama.

The local API listens only on `127.0.0.1` and is off until you enable it.

## Documentation

- [User guide](docs/user-guide.md): every feature, setting, and limit.
- [Development](docs/development.md): tests, previews, real-model checks, and code layout.
- [Desktop builds](packaging/README.md)
- [Changelog](CHANGELOG.md)
- [Design notes](DESIGN.md)

## Project status

Orchevian is approaching its 1.0 release. Remaining work:

- Signed installers for macOS, Windows, and Linux, with model runtimes included.
- Testing on clean machines for each platform.
- Checking generated Word, PowerPoint, and Excel files in the native Office apps.

Orchevian was previously called LLM Manager. Existing chats, models, projects, and
notes carry over automatically.
