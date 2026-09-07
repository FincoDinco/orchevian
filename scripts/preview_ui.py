"""Render the real Qt interface using temporary data and deterministic backends.

Run: .venv/bin/python scripts/preview_ui.py --output /tmp/llm-manager-preview
No installed models, network connections, or personal conversations are used.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from llm_engine.backends.fake import FakeBackend  # noqa: E402
from llm_engine.backends.registry import BackendRegistry  # noqa: E402
from llm_engine.domain.models import BackendName, ChatTurn, LocalModel, ModelRef  # noqa: E402
from llm_engine.store.library import LibraryService  # noqa: E402
from llm_engine.store.sqlite import SqliteStore  # noqa: E402
from llm_manager_app.main_window import MainWindow  # noqa: E402
from llm_manager_app.widgets.settings import KEY_APPEARANCE  # noqa: E402
from llm_manager_app.widgets.sidebar import CHATS, MEMORY, MODELS  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/llm-manager-preview"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    with TemporaryDirectory(prefix="llm-manager-demo-") as temporary:
        root = Path(temporary)
        store = SqliteStore(root / "demo.db")
        library = LibraryService(store)
        models = [
            LocalModel(ModelRef(BackendName.OLLAMA, name), None, size)
            for name, size in (
                ("qwen2.5-coder:14b", 9_000_000_000),
                ("deepseek-coder-v2:16b", 8_900_000_000),
                ("qwen3:8b", 5_200_000_000),
            )
        ]
        registry = BackendRegistry([FakeBackend(models=models)])
        settings = QSettings(str(root / "demo.ini"), QSettings.Format.IniFormat)
        settings.setValue(KEY_APPEARANCE, "dark")
        window = MainWindow(registry=registry, library=library, settings=settings)
        window.show()

        def capture(name: str) -> None:
            QTest.qWait(150)
            app.processEvents()
            path = args.output / f"{name}.png"
            if not window.grab().save(str(path)):
                raise RuntimeError(f"Could not save {path}")
            print(path)

        try:
            capture("welcome-dark")
            library.create_project("Writing & research")
            library.create_project("Side projects")
            for title in (
                "A reading list for the weekend",
                "Naming a new project",
                "Simplify the onboarding flow",
                "Designing a calmer workspace",
            ):
                conversation = library.create_conversation(model=models[0].ref)
                library.rename(conversation.summary.id, title)
            window._sidebar.refresh()
            window._list.refresh()
            QTest.qWait(200)
            capture("new-chat-dark")
            cid = window._list.selected_id()
            conversation = replace(
                library.get_conversation(cid),
                messages=(
                    ChatTurn(
                        "user",
                        "I'm building a local AI workspace. How can I make it feel "
                        "more focused and easier to use?",
                    ),
                    ChatTurn(
                        "assistant",
                        "Start with the conversation. Everything else should help "
                        "you get into it, stay with it, or pick it up later.\n\n"
                        "### Give the work room to breathe\n"
                        "Bring projects and recent conversations into one sidebar. Keep the "
                        "reading area comfortable, and let detailed controls open when needed.\n\n"
                        "### Make the next step obvious\n"
                        "A new conversation should help you choose a model and start writing. "
                        "In the model library, put availability and the main action together.\n\n"
                        "The result should feel like a workspace you can settle into.",
                    ),
                ),
            )
            window._chat_view.set_conversation(conversation)
            capture("chat-dark")
            window._chat_view.set_inspector_open(True)
            capture("chat-settings-dark")
            window._chat_view.set_inspector_open(False)
            window._sidebar.select_section(MODELS)
            QTest.qWait(300)
            capture("models-dark")
            window._on_appearance("light")
            capture("models-light")
            window._sidebar.select_section(CHATS)
            window._chat_view.set_conversation(conversation)
            capture("chat-light")
            window.resize(1024, 680)
            capture("chat-compact")
            window._chat_view.set_inspector_open(True)
            capture("chat-compact-settings")
            window._chat_view.set_conversation(replace(conversation, messages=()))
            capture("new-chat-compact-settings")
            window._chat_view.set_inspector_open(False)
            window._sidebar.set_collapsed(True)
            capture("sidebar-collapsed")
            window._sidebar.set_collapsed(False)
            window.resize(1280, 800)
            vault = window._memory_vault
            vault.create(
                "Local-first workspace",
                "# Local-first workspace\n\n"
                "Keep conversations, models, and knowledge on this device. "
                "The second brain belongs inside the workspace, close to the work.\n\n"
                "## Connected ideas\n\n- [[Connected notes]]\n- [[Focused writing]]\n"
                "- [[Research practice]]\n\n#workspace #local-first",
            )
            vault.create(
                "Connected notes", "# Connected notes\n\nCapture one idea per note. "
                "Connect it to related work with wiki links, then follow backlinks to see "
                "where it came from.\n\n[[Local-first workspace]] · [[Research practice]]",
            )
            vault.create("Focused writing", "# Focused writing\n\nA comfortable reading width "
                         "helps keep attention on the conversation.\n\n[[Local-first workspace]]")
            vault.create("Research practice", "# Research practice\n\nKeep source material "
                         "alongside conclusions. Revisit ideas as new evidence arrives.\n\n"
                         "[[Connected notes]] · [[Focused writing]]")
            for turn in conversation.messages:
                store.add_message(cid, turn.role, turn.content)
            fake = registry.get("ollama")
            fake.chunks = (json.dumps({"notes": [{
                "title": "A calmer AI workspace",
                "body": "# A calmer AI workspace\n\nThe user is building a local AI workspace "
                        "and wants it to feel focused and easy to use.\n\n"
                        "The conversation proposed a unified sidebar, a comfortable reading "
                        "width, and controls that open when needed.",
                "evidence": conversation.messages[0].content,
                "tags": ["workspace", "design"],
                "links": ["Notes/Local-first workspace", "Notes/Focused writing"],
            }]}),)
            window._list.refresh(select_id=cid)
            window._remember_conversation()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                app.processEvents()
                if not window._memory_busy:
                    break
                time.sleep(0.01)
            if window._memory_busy or len(vault.list_notes()) != 6:
                raise RuntimeError(window._memory._status.text())
            window._sidebar.select_section(MEMORY)
            window._on_appearance("dark")
            capture("second-brain-dark")
            window._memory._tabs.setCurrentIndex(2)
            capture("second-brain-graph-dark")
            window._on_appearance("light")
            capture("second-brain-graph-light")
            window._memory._tabs.setCurrentIndex(1)
            capture("second-brain-editor-light")
            window.resize(1024, 680)
            window._memory._tabs.setCurrentIndex(0)
            capture("second-brain-compact")
        finally:
            window.close()
            registry.close()
            store.close()


if __name__ == "__main__":
    main()
