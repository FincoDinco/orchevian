"""Render the real Qt interface using temporary data and deterministic backends.

Run: .venv/bin/python scripts/preview_ui.py --output /tmp/orchevian-preview
No installed models, network connections, or personal conversations are used.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402

from llm_engine.backends.fake import FakeBackend  # noqa: E402
from llm_engine.backends.registry import BackendRegistry  # noqa: E402
from llm_engine.domain.models import BackendName, ChatTurn, LocalModel, ModelRef  # noqa: E402
from llm_engine.hardware import GIB, Hardware  # noqa: E402
from llm_engine.services.discovery import RemoteModel  # noqa: E402
from llm_engine.services.downloads import DownloadChoice, DownloadPlan, HubFile  # noqa: E402
from llm_engine.store.library import LibraryService  # noqa: E402
from llm_engine.store.sqlite import SqliteStore  # noqa: E402
from llm_manager_app.main_window import MainWindow  # noqa: E402
from llm_manager_app.widgets.downloads_view import DownloadJob  # noqa: E402
from llm_manager_app.widgets.settings import KEY_APPEARANCE, SettingsDialog  # noqa: E402
from llm_manager_app.widgets.sidebar import CHATS, MEMORY, MODELS, PRIVATE, TEMPLATES  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/orchevian-preview"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    with TemporaryDirectory(prefix="orchevian-demo-") as temporary:
        root = Path(temporary)
        store = SqliteStore(root / "demo.db")
        library = LibraryService(store)
        library.save_template(
            name="Review a draft", description="Clear, useful feedback on a piece of writing",
            system_prompt="Help me make my writing clearer. Preserve my voice, explain the "
                          "most useful changes, and flag claims that need evidence.",
            user_prompt="Review this draft for clarity, structure, and tone:\n\n[Paste your draft]",
        )
        library.save_template(name="Explore an idea", description="Questions that develop an idea",
                              system_prompt="Ask focused questions and challenge weak assumptions.")
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

        def capture(name: str, surface=None) -> None:
            QTest.qWait(150)
            app.processEvents()
            path = args.output / f"{name}.png"
            if not (surface or window).grab().save(str(path)):
                raise RuntimeError(f"Could not save {path}")
            print(path)

        try:
            capture("welcome-dark")
            window._sidebar.select_section(TEMPLATES)
            window._templates._list.setCurrentRow(1)
            capture("templates-dark")
            window.resize(1024, 680)
            window._on_appearance("light")
            capture("templates-light-compact")
            window._on_appearance("dark")
            window.resize(1280, 800)
            window._open_settings()
            capture("settings-workspace-dark")
            settings_tabs = window._settings_dialog.findChild(QTabWidget, "settingsTabs")
            settings_tabs.setCurrentIndex(2)
            capture("settings-api-dark")
            settings_tabs.setCurrentIndex(0)
            window.resize(1024, 680)
            window._settings_dialog._appearance.setCurrentIndex(1)
            capture("settings-workspace-light-compact")
            settings_tabs.setCurrentIndex(2)
            capture("settings-api-light-compact")
            settings_tabs.setCurrentIndex(0)
            window._sidebar.select_section(PRIVATE)
            window._on_model_selected(models[0].ref)
            private_id = window._private_id
            window._chat_service._private_message(private_id, ChatTurn("user", "Help me think."))
            window._chat_service._private_message(
                private_id, ChatTurn("assistant", "What would you like to explore?"),
            )
            window._chat_view.set_conversation(window._chat_service.get_conversation(private_id))
            capture("private-chat-light-compact")
            window._on_appearance("dark")
            capture("private-chat-dark-compact")
            window._clear_private()
            window.resize(1280, 800)
            window._create_project()
            project = window._project_form
            project.set_catalog(models, {"ollama": (True, None)})
            capture("create-project-dark")
            project._model.showPopup()
            capture("project-model-menu-dark", project._model.view().window())
            project._model.hidePopup()
            project._name.setText("Writing & research")
            project._instructions.setPlainText(
                "Help me develop clear ideas. Ask focused questions "
                "and keep explanations practical."
            )
            project.choice.set_current(models[0].ref)
            project.accept()
            writing = library.list_projects()[0]
            for title in ("A reading list for the weekend", "Ideas for the next essay"):
                chat = library.create_conversation(writing.id)
                library.rename(chat.summary.id, title)
            window._sidebar.select_project(writing.id)
            window._on_catalog_listed(models, {"ollama": (True, None)})
            capture("project-home-empty-dark")
            shared = window._chat_service.documents
            (root / "Writing brief.md").write_text(
                "# Essay brief\n\nWrite a practical introduction to local AI. "
                "First draft due Friday.\n", encoding="utf-8",
            )
            (root / "Editorial calendar.csv").write_text(
                "Stage,Due\nOutline,Wednesday\nFirst draft,Friday\n", encoding="utf-8",
            )
            for name in ("Writing brief.md", "Editorial calendar.csv"):
                shared.import_project_file(writing.id, root / name, threading.Event())
            window._project_home.files.refresh()
            capture("project-home-dark")
            source_cid = chat.summary.id
            store.add_message(source_cid, "user", "When are the outline and first draft due?")
            shared.context(source_cid, "outline first draft due")
            store.add_message(source_cid, "assistant", "The outline is due Wednesday, "
                              "and the first draft is due Friday.")
            window._list.refresh(select_id=source_cid)
            window._open_project_chat(source_cid)
            capture("project-chat-files-dark")
            attachment_panel = window._chat_view.attachments
            attachment_panel.choose_project_files()
            capture("project-file-selection-dark", attachment_panel._file_selector)
            attachment_panel._file_selector.close()
            attachment_panel.show_sources()
            capture("project-source-history-dark", attachment_panel._dialogs[-1])
            attachment_panel._dialogs[-1].close()
            window._sidebar.select_project(writing.id)
            window._on_appearance("light")
            window.resize(1024, 680)
            capture("project-home-light-compact")
            window._project_home.edit_project()
            capture("project-guidance-light-compact")
            window._project_home.editor.reject()
            window._on_appearance("dark")
            window.resize(1280, 800)
            window._sidebar.select_all()
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
            # Exercise actual source rendering with a clearly labelled offline fixture.
            web = window._chat_service.web
            original_retriever = web.retriever
            web.retriever = lambda *args: {
                "provider": "Preview fixture", "warning": "", "sources": [{
                    "title": "Python documentation", "url": "https://docs.python.org/3/",
                    "excerpt": "The Python documentation includes a tutorial, library "
                               "reference, and language reference.",
                }],
            }
            message_id = store.add_message(cid, "user", "Find the Python documentation")
            try:
                web.context(cid, "Find the Python documentation", True,
                            threading.Event(), lambda _: None)
                window._chat_view.composer().web_search.setChecked(True)
                window._chat_view.web_sources.refresh()
                window._chat_view.web_sources.disclosure.setChecked(True)
                capture("web-sources-dark")
                window.resize(1024, 680)
                window._on_appearance("light")
                capture("web-sources-light-compact")
            finally:
                web.retriever = original_retriever
                with store.transaction() as conn:
                    conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                window._chat_view.composer().web_search.setChecked(False)
                window._chat_view.web_sources.refresh()
                window._on_appearance("dark")
                window.resize(1280, 800)
            brief = root / "Launch brief.md"
            brief.write_text("# Launch plan\n\nThe launch budget is $450.\n", encoding="utf-8")
            panel = window._chat_view.attachments
            document = panel.service.import_file(cid, brief, threading.Event())
            panel.disclosure.setChecked(True)
            panel.refresh()
            capture("chat-attachments-dark")
            window.resize(1024, 680)
            window._on_appearance("light")
            capture("chat-attachments-light-compact")
            panel.preview()
            capture("attachment-reader-light", panel._dialogs[-1])
            panel._dialogs[-1].close()
            panel.service.remove_draft(cid, document.id)
            panel.refresh()
            window._on_appearance("dark")
            window.resize(1280, 800)
            window._chat_view.set_inspector_open(True)
            capture("chat-settings-dark")
            window._chat_view.set_inspector_open(False)
            transcript = window._chat_view.transcript()
            transcript.set_turns([conversation.messages[0]])
            window._chat_view.composer().set_generating(True)
            transcript.begin_stream()
            transcript.append_stream(
                "<think>I’ll consider the layout, interaction, and reading experience.\n"
                "Keep frequent actions easy to find and let optional details stay out of the way."
            )
            window._model_status_timer.stop()
            window._model_activity.set_activity(True, "Model is thinking…")
            window._model_activity._reveal.stop()
            window._model_activity_action.setVisible(True)  # Skip the delay and fade.
            capture("chat-thinking-collapsed-dark")
            window._on_appearance("light")
            window.resize(1024, 680)
            capture("model-activity-light-compact")
            window._on_appearance("dark")
            window.resize(1280, 800)
            transcript._thought_toggle.click()
            capture("chat-thinking-expanded-dark")
            transcript.append_stream(
                "</think>Start with a clear sidebar and a focused conversation."
            )
            transcript.finish_stream(parse_markdown=True)
            window._chat_view.composer().set_generating(False)
            window._sync_model_activity()
            window._model_status_timer.start()
            capture("chat-thinking-complete-dark")
            window._chat_view.set_conversation(conversation)
            window._sidebar.select_section(MODELS)
            QTest.qWait(300)
            capture("models-dark")
            window._models.storage._toggle.setChecked(True)
            capture("model-storage-dark")
            window._models.storage._toggle.setChecked(False)
            window._models.rename_selected("My writing helper")
            capture("model-custom-name-dark")
            window._models.rename_selected("")
            window._models._details_toggle.click()
            capture("model-details-dark")
            window._models._details_toggle.click()
            window._models._discovery.apply_results(
                [RemoteModel(
                    f"example/{name}", "mlx", 12000, memory * GIB,
                    "Estimated from name at 4-bit, including 2 GB runtime/context allowance",
                ) for name, memory in (
                    ("Everyday-3B-4bit", 4), ("Chat-8B-4bit", 7), ("Code-7B-4bit", 6),
                )],
                Hardware("Darwin", "arm64", 24 * GIB, 18 * GIB, cpu_count=12), True,
            )
            window._models._tabs.setCurrentIndex(1)
            capture("discovery-dark")
            window._on_appearance("light")
            capture("discovery-light")
            window.resize(1024, 680)
            capture("discovery-compact")
            discovery = window._models._discovery
            remote = discovery.results.currentItem().data(Qt.ItemDataRole.UserRole)
            discovery.apply_plan(DownloadPlan(remote, "a" * 40, (
                DownloadChoice("Complete MLX model", (HubFile("model.safetensors", 5 * GIB),)),
            )))
            capture("download-ready-compact")
            manager = window._models.downloads
            for index in range(discovery.results.count()):
                model = discovery.results.item(index).data(Qt.ItemDataRole.UserRole)
                choice = DownloadChoice(
                    "Complete MLX model", (HubFile("model.safetensors", 5 * GIB),),
                )
                plan = DownloadPlan(model, "a" * 40, (choice,))
                manager.jobs[index + 1] = DownloadJob(
                    index + 1, plan, choice,
                    state="Downloading" if index < 2 else "Queued",
                    done=(2 - index) * GIB if index < 2 else 0,
                    message=f"{2 - index}.00 of 5.00 GB" if index < 2 else "Waiting for a slot",
                )
            manager.changed.emit()
            capture("browsing-with-downloads-compact")
            window._show_downloads()
            QTest.qWait(100)
            window._downloads_popover.grab().save(str(args.output / "downloads-popover-light.png"))
            window._models.download_view._disclosures[1].click()
            QTest.qWait(100)
            window._downloads_popover.grab().save(str(args.output / "downloads-expanded-light.png"))
            window._on_appearance("dark")
            QTest.qWait(100)
            window._downloads_popover.grab().save(str(args.output / "downloads-expanded-dark.png"))
            window._downloads_popover.hide()
            window._on_appearance("light")
            window._sidebar.select_section(MODELS)
            for name, dialog in (
                ("discovery-options", discovery._options_dialog),
                ("discovery-details", discovery._details_dialog),
            ):
                dialog.show()
                QTest.qWait(100)
                dialog.grab().save(str(args.output / f"{name}.png"))
                dialog.hide()
            preferences = SettingsDialog(
                window, settings=settings,
                config_get=lambda: {"model_dir": str(root / "models"), "api_port": 8080},
                db_path=root / "demo.db", log_path=root / "engine.log",
            )
            preferences.show()
            QTest.qWait(100)
            preferences.grab().save(str(args.output / "settings-light.png"))
            preferences.close()
            window.resize(1280, 840)
            window._models._tabs.setCurrentIndex(0)
            capture("models-light")
            window._models.storage._toggle.setChecked(True)
            capture("model-storage-light")
            window._models.storage._toggle.setChecked(False)
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
                "Memoria belongs inside the workspace, close to the work.\n\n"
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
            capture("memoria-dark")
            window._memory._tabs.setCurrentIndex(2)
            capture("memoria-graph-dark")
            window._on_appearance("light")
            capture("memoria-graph-light")
            window._memory._tabs.setCurrentIndex(1)
            capture("memoria-editor-light")
            window.resize(1024, 680)
            window._memory._tabs.setCurrentIndex(0)
            capture("memoria-compact")
        finally:
            window.close()
            registry.close()
            store.close()


if __name__ == "__main__":
    main()
