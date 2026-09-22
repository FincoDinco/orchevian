from __future__ import annotations

import json
import time

from PySide6.QtWidgets import QDialog

from llm_engine.artifacts.generators import generate
from llm_engine.backends.registry import BackendRegistry
from llm_engine.services.artifacts import ArtifactService
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from llm_manager_app.widgets.chat_view import ChatView
from test_app_shell import _qapp, _window
from test_artifacts import StructuredBackend, specification


def test_creation_choice_revision_and_preview_are_scoped_per_chat(tmp_path):
    import threading

    app = _qapp()
    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        service = ArtifactService(store)
        first = library.create_conversation()
        second = library.create_conversation()
        view = ChatView()
        view.artifacts.service = service
        try:
            view.set_conversation(first)
            view.composer().create_files.setChecked(True)
            artifacts = service.publish(
                first.summary.id, [generate(specification("docx"))], [], threading.Event()
            )
            view.artifacts._revise(artifacts[0])
            assert view.artifacts.request() == {"revision": artifacts[0].id}
            view.set_conversation(second)
            assert view.artifacts.request() is None
            view.composer().create_files.setChecked(True)
            assert view.artifacts.request() == {}
            view.set_conversation(first)
            assert view.artifacts.request() == {"revision": artifacts[0].id}
            view.artifacts._preview(artifacts[0])
            app.processEvents()
            assert view.findChildren(QDialog)
        finally:
            view.close()


def test_gui_routes_creation_request_and_shows_files(tmp_path):
    app = _qapp()
    backend = StructuredBackend(
        [
            json.dumps(
                {
                    "tool": "create_documents",
                    "files": [
                        specification("pptx").model_dump(),
                        specification("pdf", "form").model_dump(),
                    ],
                }
            )
        ]
    )
    window, store, library = _window(tmp_path, registry=BackendRegistry([backend]))
    window._settings.setValue("memory/automatic", False)
    # Avoid a second model call for automatic memory extraction in this UI test.
    window._worker.done.disconnect(window._schedule_automatic_memory)
    try:
        cid = library.create_conversation(model=backend.list_models()[0].ref).summary.id
        window._list.refresh(select_id=cid)
        window._show_selected_chat()
        composer = window._chat_view.composer()
        composer.create_files.setChecked(True)
        composer.set_text("Create a slide deck and fillable intake form")
        composer.submit()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if window._chat_service.artifacts.list(cid) and not window._chat_service._generating:
                break
            time.sleep(0.01)
        app.processEvents()
        assert len(window._chat_service.artifacts.list(cid)) == 2
        assert not window._chat_view.is_streaming()
        assert "Generated files (2)" == window._chat_view.artifacts.disclosure.text()
    finally:
        window.close()
        store.close()


def test_project_home_keeps_creation_choice_with_each_draft(tmp_path):
    _qapp()
    window, store, library = _window(tmp_path)
    try:
        first = library.create_project("First")
        second = library.create_project("Second")
        home = window._project_home
        home.set_project(first)
        home.composer.create_files.setChecked(True)
        home.composer.set_text("Create a proposal")
        home.set_project(second)
        assert not home.composer.create_files.isChecked()
        home.set_project(first)
        assert home.composer.create_files.isChecked()
        assert home.composer.text() == "Create a proposal"
    finally:
        window.close()
        store.close()


def test_private_preview_closes_and_discards_selection_when_chat_is_cleared(tmp_path):
    import threading
    from dataclasses import replace

    app = _qapp()
    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        conversation = library.create_conversation()
        private = replace(conversation, summary=replace(conversation.summary, id=-1))
        service = ArtifactService(store)
        artifact = service.publish(-1, [generate(specification("txt"))], [], threading.Event())[0]
        view = ChatView()
        view.artifacts.service = service
        try:
            view.set_conversation(private)
            view.artifacts._preview(artifact)
            view.artifacts._revise(artifact)
            assert len(view.artifacts._dialogs) == 1
            service.discard_private(-1)
            view.clear_private(-1)
            app.processEvents()
            assert view.artifacts._dialogs == []
            assert -1 not in view.artifacts._requests
            assert not view.composer().create_files.isChecked()
        finally:
            view.close()
