from __future__ import annotations

import json
import threading
import time
from datetime import date

import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import ChatTurn, GenerationParams
from llm_engine.services import documents
from llm_engine.services.chat import ChatService
from llm_engine.services.documents import CONTEXT_CHARS, DocumentService
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore
from test_documents import pdf_bytes


@pytest.fixture
def stack(tmp_path):
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    service = DocumentService(store)
    project = library.create_project("Launch")
    yield store, library, service, project.id
    store.close()


def upload(service, pid, tmp_path, text="The launch deadline is Friday.", name="brief.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return service.import_project_file(pid, path, threading.Event())


def context(store, service, cid, question="launch budget deadline"):
    store.add_message(cid, "user", question)
    return service.context(cid, question)


def test_pdf_word_and_excel_shared_by_two_chats_but_not_private_or_api(stack, tmp_path):
    from docx import Document
    from openpyxl import Workbook

    store, library, service, pid = stack
    (tmp_path / "budget.pdf").write_bytes(pdf_bytes())
    word = Document()
    word.add_heading("Launch", 1)
    word.add_paragraph("The launch deadline is Friday.")
    word.save(tmp_path / "brief.docx")
    book = Workbook()
    book.active.title = "Staffing"
    book.active.append(["Launch staff", 7])
    book.save(tmp_path / "staff.xlsx")
    for name in ("budget.pdf", "brief.docx", "staff.xlsx"):
        service.import_project_file(pid, tmp_path / name, threading.Event())

    class RecordingBackend(FakeBackend):
        def __init__(self):
            super().__init__()
            self.prompts = []

        def stream_generate(self, handle, messages, params, cancel):
            self.prompts.append(messages)
            yield "The deadline is Friday."

    backend = RecordingBackend()
    session = ModelSession(BackendRegistry([backend]))
    chat = ChatService(library, session)
    chat.today = lambda: date(2026, 9, 21)  # A Monday, so "Friday" only comes from the brief.
    ref = backend.list_models()[0].ref
    other = library.create_project("Unrelated")
    try:
        for project in (pid, pid, other.id, None):
            cid = library.create_conversation(project_id=project, model=ref).summary.id
            chat.send(cid, "What are the launch budget, deadline, and staff count?")
            chat._worker_thread.join(5)
            assert not chat._worker_thread.is_alive()
            prompt = "\n".join(turn.content for turn in backend.prompts[-1])
            sources = service.sources(cid)
            if project == pid:
                assert "450 dollars" in prompt and "Friday" in prompt and "B1: 7" in prompt
                assert {source["name"] for source in sources} == {
                    "budget.pdf", "brief.docx", "staff.xlsx",
                }
                assert all(source["version"] == 1 for source in sources)
                assert any(source["location"] == "Page 1" for source in sources)
                assert any("Staffing" in source["location"] for source in sources)
            else:
                assert not sources
                assert "450 dollars" not in prompt and "Friday" not in prompt
        private = chat.create_private(ref).summary.id
        chat.send(private, "What is the launch budget?")
        chat._worker_thread.join(5)
        assert service.project_files_for_chat(private) == []
        assert not any("450 dollars" in turn.content for turn in backend.prompts[-1])
        list(chat.stream_external(ref, [ChatTurn("user", "Launch budget?")],
                                  GenerationParams(), threading.Event()))
        assert backend.prompts[-1] == [ChatTurn("user", "Launch budget?")]
    finally:
        session.force_unload()


def test_replacement_removal_and_project_deletion_preserve_reply_versions(stack, tmp_path):
    store, library, service, pid = stack
    doc = upload(service, pid, tmp_path)
    cid = library.create_conversation(project_id=pid).summary.id
    assert "Friday" in context(store, service, cid, "First question")
    first = service.sources(cid)[0]
    replacement = tmp_path / "new-brief.txt"
    replacement.write_text("The launch deadline is Monday.")
    updated = service.import_project_file(pid, replacement, threading.Event(), replacing=doc)
    assert updated.id == doc.id and updated.version == 2
    assert "Monday" in context(store, service, cid, "Second question")
    assert [items[0]["version"] for _, items in service.source_history(cid)] == [2, 1]
    assert service.source_history(cid)[1][1][0] == first
    with pytest.raises(EngineError, match="version is no longer"):
        service.project_original(pid, doc)
    service.remove_project_file(pid, updated)
    assert context(store, service, cid, "Third question") == ""
    assert first in service.sources(cid)
    upload(service, pid, tmp_path, "New project document")
    library.delete_project(pid)
    assert library.get_conversation(cid).summary.project_id is None
    assert service.list_project(pid) == []
    assert context(store, service, cid, "After deletion") == ""
    assert first in service.sources(cid)


def test_selection_originals_and_versions_survive_restart(stack, tmp_path):
    store, library, service, pid = stack
    doc = upload(service, pid, tmp_path)
    cid = library.create_conversation(project_id=pid).summary.id
    second = library.create_conversation(project_id=pid).summary.id
    service.set_project_file_enabled(cid, doc.id, False)
    assert context(store, service, cid) == ""
    assert "Friday" in context(store, service, second)
    (tmp_path / "brief.txt").unlink()
    store.close()
    with SqliteStore(store.path) as reopened:
        persisted = DocumentService(reopened)
        assert persisted.list_project(pid) == [doc]
        assert persisted.project_original(pid, doc) == b"The launch deadline is Friday."
        assert persisted.project_files_for_chat(cid) == [(doc, False)]
        assert persisted.project_files_for_chat(second) == [(doc, True)]
        assert "Friday" in persisted.sources(second)[0]["text"]


def test_upgrade_from_chat_documents_keeps_originals_and_history(stack, tmp_path):
    store, library, service, pid = stack
    cid = library.create_conversation(project_id=pid).summary.id
    path = tmp_path / "chat.txt"
    path.write_text("Existing chat attachment")
    doc = service.import_file(cid, path, threading.Event())
    # Recreate the prior schema in this temporary test database.
    with store.transaction() as conn:
        conn.execute("DROP TABLE project_document_exclusions")
        conn.execute("DROP TABLE project_documents")
        conn.execute("DELETE FROM schema_migrations WHERE version = 4")
    store.close()
    with SqliteStore(store.path) as upgraded:
        assert upgraded.schema_version() == 7
        migrated = DocumentService(upgraded)
        assert migrated.original(cid, doc.id) == b"Existing chat attachment"
        assert LibraryService(upgraded).get_conversation(cid).summary.project_id == pid
        assert upload(migrated, pid, tmp_path).version == 1


def test_move_switches_retrieval_and_keeps_chat_attachments(stack, tmp_path):
    store, library, service, pid = stack
    first = upload(service, pid, tmp_path, "First project secret", "first.txt")
    other = library.create_project("Second")
    second = upload(service, other.id, tmp_path, "Second project secret", "second.txt")
    cid = library.create_conversation(project_id=pid).summary.id
    path = tmp_path / "attached.txt"
    path.write_text("Chat-owned note")
    attached = service.import_file(cid, path, threading.Event())
    message = store.add_message(cid, "user", "Read files")
    with store.transaction() as conn:
        conn.execute("UPDATE documents SET message_id = ? WHERE id = ?", (message, attached.id))
    assert "First project secret" in context(store, service, cid)
    library.move(cid, other.id)
    prompt = context(store, service, cid)
    assert "Second project secret" in prompt and "First project secret" not in prompt
    assert "Chat-owned note" in prompt
    with pytest.raises(EngineError, match="no longer in"):
        service.set_project_file_enabled(cid, first.id, True)
    library.move(cid, None)
    prompt = context(store, service, cid)
    assert "Second project secret" not in prompt and "Chat-owned note" in prompt
    library.delete_conversation(cid)
    assert service.list_project(pid) == [first] and service.list_project(other.id) == [second]


def test_failed_cancelled_and_stale_replacements_keep_current_version(stack, tmp_path):
    _, _, service, pid = stack
    original = upload(service, pid, tmp_path)
    corrupt = tmp_path / "broken.docx"
    corrupt.write_bytes(b"broken")
    with pytest.raises(EngineError, match="Could not read"):
        service.import_project_file(pid, corrupt, threading.Event(), replacing=original)
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(EngineError, match="cancelled"):
        service.import_project_file(pid, tmp_path / "brief.txt", cancelled, replacing=original)
    assert service.list_project(pid) == [original]
    replacement = service.import_project_file(
        pid, tmp_path / "brief.txt", threading.Event(), replacing=original,
    )
    with pytest.raises(EngineError, match="changed or was removed"):
        service.import_project_file(pid, tmp_path / "brief.txt", threading.Event(),
                                    replacing=original)
    with pytest.raises(EngineError, match="changed or was removed"):
        service.remove_project_file(pid, original)
    assert service.list_project(pid) == [replacement]


def test_deleting_project_during_import_does_not_restore_files(stack, tmp_path, monkeypatch):
    _, library, service, pid = stack
    def parse(data, suffix, cancel, **options):
        library.delete_project(pid)
        return (*documents.extract(data, suffix), [])
    monkeypatch.setattr(documents, "parse_isolated", parse)
    with pytest.raises(EngineError, match="project was deleted"):
        upload(service, pid, tmp_path)
    assert service.list_project(pid) == []


def test_shared_context_budget_and_project_file_limit(stack, tmp_path, monkeypatch):
    store, library, service, pid = stack
    monkeypatch.setattr(documents, "MAX_FILES", 2)
    first = upload(service, pid, tmp_path, "budget " * 15000, "large.txt")
    upload(service, pid, tmp_path, "The launch date is Friday", "date.txt")
    with pytest.raises(EngineError, match="already has 20 files"):
        upload(service, pid, tmp_path, name="extra.txt")
    cid = library.create_conversation(project_id=pid).summary.id
    context(store, service, cid)
    sources = service.sources(cid)
    assert 0 < len(json.dumps(sources, ensure_ascii=False)) <= CONTEXT_CHARS
    assert len(sources) < len(first.segments)


def wait_ui(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_project_upload_switch_replace_remove_and_chat_selection_ui(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox, QPlainTextEdit

    from test_app_shell import _qapp, _window

    app = _qapp()
    window, store, library = _window(tmp_path)
    first = library.create_project("First")
    second = library.create_project("Second")
    home = window._project_home
    files = home.files
    path = tmp_path / "brief.txt"
    path.write_text("Project launch brief")
    try:
        window._sidebar.refresh()
        window._sidebar.select_project(first.id)
        home.composer.set_text("Keep my draft")
        files.import_paths([path])
        assert home.composer._importing
        window._sidebar.select_project(second.id)
        assert not home.composer._importing
        wait_ui(app, lambda: files.thread is None)
        assert files.files.count() == 0
        window._sidebar.select_project(first.id)
        assert files.files.count() == 1
        assert home.composer.text() == "Keep my draft"
        path.write_text("Revised brief")
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_: (str(path), ""))
        files.replace_selected()
        wait_ui(app, lambda: files.thread is None)
        assert files._selected().version == 2
        cid = library.create_conversation(project_id=first.id).summary.id
        window._on_selected(cid)
        panel = window._chat_view.attachments
        assert not panel.project_files.isHidden()
        panel.choose_project_files()
        checkbox = panel._file_selector.findChild(QCheckBox)
        checkbox.setChecked(False)
        assert panel.service.project_files_for_chat(cid)[0][1] is False
        assert panel._file_selector is not None
        files.preview()
        assert "Revised brief" in files._dialogs[-1].findChild(QPlainTextEdit).toPlainText()
        files._dialogs[-1].close()
        monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
        files.remove_selected()
        assert files.files.count() == 0
        assert panel.project_files.isHidden()
        assert panel._file_selector is None
    finally:
        window.close()
        store.close()


def test_closing_during_project_import_cancels_and_keeps_existing_file(tmp_path, monkeypatch):
    from test_app_shell import _qapp, _window

    app = _qapp()
    window, store, library = _window(tmp_path)
    project = library.create_project("Closing test")
    files = window._project_home.files
    original = upload(files.service, project.id, tmp_path)
    window._sidebar.refresh(select_project_id=project.id)
    entered = threading.Event()

    def blocked(data, suffix, cancel, **options):
        entered.set()
        assert cancel.wait(3)
        raise EngineError("cancelled", "Import cancelled.")

    monkeypatch.setattr(documents, "parse_isolated", blocked)
    closed = False
    try:
        files.import_paths([tmp_path / "brief.txt"])
        wait_ui(app, entered.is_set)
        assert window.close()
        closed = True
        assert files.thread is None
        assert files.service.list_project(project.id) == [original]
        store.close()
        # Queued thread completion must not access the now-closed database.
        app.processEvents()
    finally:
        if not closed:
            window.close()
        store.close()
