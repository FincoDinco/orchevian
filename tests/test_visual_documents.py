from __future__ import annotations

import base64
import io
import json
import multiprocessing
import threading
from dataclasses import replace

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.ollama import OLLAMA_BASE_URL, OllamaBackend
from llm_engine.backends.process import ProcessBackend
from llm_engine.backends.protocol import ModelHandle
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import ChatTurn, GenerationParams
from llm_engine.services.chat import ChatService
from llm_engine.services.documents import DocumentService, parse_isolated
from llm_engine.services.session import ModelSession
from llm_engine.services.visuals import (
    MAX_TURN_IMAGES,
    normalize_image,
    ocr_text,
    page_selection,
    read_visuals,
)
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore


def picture_bytes(fmt="PNG", *, text=True):
    image = Image.new("RGB", (1000, 240), "white")
    if text:
        ImageDraw.Draw(image).text(
            (30, 60),
            "Launch budget 450 dollars",
            fill="black",
            font=ImageFont.load_default(size=48),
        )
    output = io.BytesIO()
    image.save(output, format=fmt)
    return output.getvalue()


def scan_bytes(pages=1):
    pictures = [Image.open(io.BytesIO(picture_bytes())) for _ in range(pages)]
    output = io.BytesIO()
    pictures[0].save(output, format="PDF", save_all=True, append_images=pictures[1:])
    return output.getvalue()


@pytest.mark.parametrize("fmt,suffix", [("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")])
def test_images_are_decoded_and_normalized_without_metadata(fmt, suffix):
    pages, segments, warning = read_visuals(picture_bytes(fmt), suffix)
    assert len(pages) == 1 and pages[0].location == "Image 1"
    with Image.open(io.BytesIO(pages[0].data)) as image:
        assert image.format == "JPEG" and image.size == (1000, 240)
        assert not image.getexif()
    assert segments[0][0] == "Image 1 · OCR"
    assert warning


def test_real_local_ocr_reads_a_scanned_page():
    text, warning = ocr_text(normalize_image(picture_bytes()))
    if "unavailable" in warning:
        pytest.skip("Install Tesseract to run real OCR acceptance")
    assert "450" in text and "budget" in text.lower()
    pages, segments, _ = read_visuals(scan_bytes(), ".pdf")
    assert pages[0].location == "Page 1"
    assert any("450" in text and location == "Page 1 · OCR" for location, text in segments)


def test_missing_ocr_preserves_picture_and_reports_setup(monkeypatch):
    import llm_engine.services.visuals as visuals

    monkeypatch.setattr(visuals.ctypes.util, "find_library", lambda name: None)

    def unavailable(*args):
        raise OSError("not installed")

    monkeypatch.setattr(visuals.ctypes, "CDLL", unavailable)
    pages, segments, warning = read_visuals(picture_bytes(), ".png")
    assert pages and not segments[0][1]
    assert "Install Tesseract" in warning and "vision model" in warning


def test_invalid_oversized_animated_images_are_rejected():
    with pytest.raises(Exception):
        normalize_image(b"not a picture")
    image = Image.new("1", (5001, 5000))
    output = io.BytesIO()
    image.save(output, format="PNG")
    with pytest.raises(ValueError, match="25 megapixels"):
        normalize_image(output.getvalue())
    output = io.BytesIO()
    frames = [Image.new("RGB", (10, 10), color) for color in ("red", "blue")]
    frames[0].save(output, format="PNG", save_all=True, append_images=frames[1:])
    with pytest.raises(ValueError, match="Animated"):
        normalize_image(output.getvalue())


@pytest.mark.parametrize("value", ["0", "1-9", "3-1", "abc", "1,2,3,4,5,6,7,8,9"])
def test_page_selection_limits(value):
    with pytest.raises(ValueError):
        page_selection(value)


def test_selected_pdf_pages_and_process_cancellation():
    assert page_selection("1-3, 7, 2") == (1, 2, 3, 7)
    assert page_selection("") is None
    cancel = threading.Event()
    result = parse_isolated(scan_bytes(3), ".pdf", cancel, include_visuals=True, pages=(2,))
    assert [p.location for p in result[2]] == ["Page 2"]
    assert "pages 2 of 3" in result[1]
    with pytest.raises(EngineError, match="between 1 and 3"):
        parse_isolated(scan_bytes(3), ".pdf", cancel, include_visuals=True, pages=(4,))
    previous = {p.pid for p in multiprocessing.active_children()}
    with pytest.raises(EngineError, match="cancelled"):
        parse_isolated(
            scan_bytes(3),
            ".pdf",
            cancel,
            include_visuals=True,
            progress=lambda message: cancel.set(),
        )
    assert {p.pid for p in multiprocessing.active_children()} == previous


@pytest.fixture
def library_stack(tmp_path):
    store = SqliteStore(tmp_path / "library.db")
    library = LibraryService(store)
    yield store, library, DocumentService(store)
    store.close()


def test_project_visual_versions_selection_and_retained_sources(library_stack, tmp_path):
    store, library, service = library_stack
    project = library.create_project("Visual research")
    path = tmp_path / "chart.png"
    path.write_bytes(picture_bytes())
    doc = service.import_project_file(project.id, path, threading.Event())
    first = library.create_conversation(project_id=project.id).summary.id
    second = library.create_conversation(project_id=project.id).summary.id
    outside = library.create_conversation().summary.id
    for cid in (first, second):
        store.add_message(cid, "user", "What is the budget?")
        images = []
        context = service.context(cid, "budget", image_payload=images)
        assert images and "Image supplied" in context
        assert service.sources(cid)[0]["location"] == "Image 1"
    assert service.context(outside, "budget", image_payload=[]) == ""
    source = service.sources(first)[0]
    service.set_project_file_enabled(second, doc.id, False)
    assert service.context(second, "budget", image_payload=[]) == ""
    service.set_use_images(first, False)
    store.close()
    with SqliteStore(store.path) as reopened:
        service = DocumentService(reopened)
        assert not service.use_images(first)
        assert not service.project_files_for_chat(second)[0][1]
        assert service.visual_data(first, doc, 0)
        path.write_bytes(picture_bytes(text=False))
        updated = service.import_project_file(project.id, path, threading.Event(), replacing=doc)
        with pytest.raises(EngineError, match="version"):
            service.visual_data(first, doc, 0)
        store = reopened
        store.add_message(first, "user", "Read the new chart")
        service.context(first, "chart", image_payload=[])
        assert service.sources(first)[0]["version"] == 2
        assert source in service.sources(first)
        service.remove_project_file(project.id, updated)
        assert service.context(first, "chart", image_payload=[]) == ""
        assert source in service.sources(first)
        with store.locked() as conn:
            assert conn.execute("SELECT count(*) FROM document_visuals").fetchone()[0] == 0


class VisionBackend(FakeBackend):
    """Worker discovers vision on load, exercising capability transport to the parent."""

    def load(self, model, options=None):
        return super().load(replace(model, supports_images=True), options)

    def stream_generate(self, handle, messages, params, cancel):
        assert len(messages[-1].images) == 1
        assert messages[-1].images[0].startswith(b"\xff\xd8")
        yield "I received the image"


def test_images_and_capabilities_cross_the_actual_model_process():
    catalog = FakeBackend()
    backend = ProcessBackend(catalog, VisionBackend)
    session = ModelSession(BackendRegistry([backend]))
    model = catalog.list_models()[0]
    try:
        loaded = session.load(model.ref)
        assert loaded.supports_images is True
        data = read_visuals(picture_bytes(), ".png")[0][0].data
        assert (
            "".join(
                session.generate(
                    [ChatTurn("user", "Read it", images=(data,))],
                    GenerationParams(),
                    threading.Event(),
                )
            )
            == "I received the image"
        )
    finally:
        session.force_unload()


def test_chat_vision_guard_text_opt_out_and_private_cleanup(library_stack, tmp_path):
    store, library, _ = library_stack

    class Recording(FakeBackend):
        prompts = []

        def stream_generate(self, handle, messages, params, cancel):
            self.prompts.append(messages)
            yield "Recorded"

    fake = Recording()
    session = ModelSession(BackendRegistry([fake]))
    chat = ChatService(library, session)
    errors = []
    chat.on_error = lambda cid, error: errors.append(error.code)
    cid = library.create_conversation(model=fake.list_models()[0].ref).summary.id
    path = tmp_path / "picture.png"
    path.write_bytes(picture_bytes())
    doc = chat.documents.import_file(cid, path, threading.Event())
    chat.send(cid, "What is in this picture?")
    chat._worker_thread.join(10)
    assert not fake.prompts and errors == ["vision_required"]
    # No visual request is silently passed to a text-only model.
    chat.documents.set_use_images(cid, False)
    if not doc.segments:
        with store.transaction() as conn:
            conn.execute(
                "UPDATE documents SET segments = ? WHERE id = ?",
                (json.dumps([["Image 1 · OCR", "Launch budget 450 dollars"]]), doc.id),
            )
    chat.send(cid, "Read the extracted budget")
    chat._worker_thread.join(10)
    assert fake.prompts and not any(t.images for t in fake.prompts[-1])
    assert "NOT seen" in fake.prompts[-1][0].content
    private = chat.create_private(fake.list_models()[0].ref).summary.id
    chat.documents.import_file(private, path, threading.Event())
    chat.documents.commit_private(private)
    images = []
    chat.documents.context(private, "budget", image_payload=images)
    assert images
    chat.discard_private(private)
    assert not chat.documents.list(private) and not chat.documents.sources(private)
    assert private not in chat.documents._private_images
    with store.locked() as conn:
        assert conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    session.force_unload()


@pytest.mark.parametrize("capabilities", [["completion", "vision"], ["completion"], None])
def test_ollama_capability_guard_and_real_image_payload(capabilities):
    calls = []
    image = read_visuals(picture_bytes(), ".png")[0][0].data

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": capabilities})
        body = json.loads(request.content)
        assert base64.b64decode(body["messages"][0]["images"][0]) == image
        return httpx.Response(200, text=json.dumps({"message": {"content": "450"}, "done": True}))

    with httpx.Client(base_url=OLLAMA_BASE_URL, transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend(client)
        handle = ModelHandle(FakeBackend().list_models()[0])
        stream = backend.stream_generate(
            handle,
            [ChatTurn("user", "Read", images=(image,))],
            GenerationParams(),
            threading.Event(),
        )
        if capabilities and "vision" in capabilities:
            assert list(stream) == ["450"]
        else:
            with pytest.raises(EngineError) as exc:
                list(stream)
            assert exc.value.code == "vision_required" and "/api/chat" not in calls


def test_image_context_is_bounded_and_only_sent_attachments_are_used(library_stack, tmp_path):
    store, library, service = library_stack
    cid = library.create_conversation().summary.id
    project = library.create_project("Scans")
    cid = library.create_conversation(project_id=project.id).summary.id
    path = tmp_path / "scans.pdf"
    path.write_bytes(scan_bytes(6))
    service.import_project_file(project.id, path, threading.Event())
    draft = tmp_path / "draft.png"
    draft.write_bytes(picture_bytes())
    doc = service.import_file(cid, draft, threading.Event())
    store.add_message(cid, "user", "Budget")
    images = []
    context = service.context(cid, "budget", image_payload=images)
    assert len(images) == MAX_TURN_IMAGES and "4 of 6" in context
    assert all(s["document_id"] != doc.id for s in service.sources(cid))
    assert len(json.dumps(service.sources(cid), ensure_ascii=False)) <= 8000


def test_chat_uses_vision_without_persisting_image_bytes_in_messages(library_stack, tmp_path):
    store, library, _ = library_stack
    backend = ProcessBackend(FakeBackend(), VisionBackend)
    session = ModelSession(BackendRegistry([backend]))
    errors = []
    chat = ChatService(library, session, on_error=lambda cid, error: errors.append(error))
    cid = library.create_conversation(model=FakeBackend().list_models()[0].ref).summary.id
    path = tmp_path / "image.png"
    path.write_bytes(picture_bytes())
    try:
        doc = chat.documents.import_file(cid, path, threading.Event())
        chat.send(cid, "Read this picture")
        chat._worker_thread.join(10)
        assert not chat._worker_thread.is_alive() and not errors
        assert library.get_conversation(cid).messages[-1].content == "I received the image"
        assert all(not turn.images for turn in library.get_conversation(cid).messages)
        assert chat.documents.sources(cid)[0]["document_id"] == doc.id
        chat.regenerate(cid)
        chat._worker_thread.join(10)
        assert not errors
        assert library.get_conversation(cid).messages[-1].content == "I received the image"
    finally:
        session.force_unload()


def test_model_request_image_limits():
    from llm_engine.services.visuals import MAX_IMAGE_BYTES, validate_image_inputs

    for turns in (
        [ChatTurn("user", "", images=(b"x",) * 5)],
        [ChatTurn("system", "", images=(b"x",))],
        [ChatTurn("user", "", images=(b"",))],
        [ChatTurn("user", "", images=(b"x" * (MAX_IMAGE_BYTES + 1),))],
    ):
        with pytest.raises(EngineError):
            validate_image_inputs(turns)


def test_ui_picture_preview_visual_mode_and_private_dialog_cleanup(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel

    from test_app_shell import _qapp, _window

    app = _qapp()
    window, store, library = _window(tmp_path)
    panel = window._chat_view.attachments
    path = tmp_path / "image.png"
    path.write_bytes(picture_bytes())
    cid = library.create_conversation().summary.id
    private = window._chat_service.create_private().summary.id
    try:
        window._on_selected(cid)
        panel.service.import_file(cid, path, threading.Event())
        panel.refresh()
        assert panel.visual_mode.isChecked()
        panel.visual_mode.click()
        assert not panel.service.use_images(cid)
        panel.preview()
        dialog = panel._dialogs[0]
        assert any(
            isinstance(label.pixmap(), QPixmap) and not label.pixmap().isNull()
            for label in dialog.findChildren(QLabel)
        )
        dialog.close()
        panel.set_conversation(private)
        panel.service.import_file(private, path, threading.Event())
        panel.refresh()
        panel.preview()
        assert panel._dialogs
        panel.set_conversation(cid)
        app.processEvents()
        assert not panel._dialogs
        assert not panel.visual_mode.isChecked()
        panel.set_generating(True)
        assert not panel.visual_mode.isEnabled()
        assert panel.files.item(0).data(Qt.ItemDataRole.UserRole).images
    finally:
        window._chat_service.discard_private(private)
        window.close()
        store.close()
