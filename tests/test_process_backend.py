from __future__ import annotations

import json
import multiprocessing
import os
import signal
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.ollama import OllamaBackend
from llm_engine.backends.process import ProcessBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ChatTurn, GenerationParams, LocalModel, ModelRef
from llm_engine.services.chat import ChatService
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore


def model(name: str) -> LocalModel:
    return LocalModel(ModelRef(BackendName.GGUF, name), None, 0)


class StuckRuntime(FakeBackend):
    """Spawnable stand-in for native code that never checks cancellation."""

    def __init__(self, marker: Path) -> None:
        super().__init__(name=BackendName.GGUF)
        self.marker = marker

    def load(self, model, options=None):
        if model.ref.name == "crash":
            os._exit(7)
        if model.ref.name == "stuck":
            if os.name == "posix":
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            self.marker.write_text(str(os.getpid()))
            while True:
                time.sleep(1)
        return super().load(model, options)

    def stream_generate(self, handle, messages, params, cancel):
        if handle.model.ref.name == "waiting":
            self.marker.write_text(str(os.getpid()))
            while True:
                time.sleep(1)
        if handle.model.ref.name == "partial":
            yield "Saved partial response"
            self.marker.write_text(str(os.getpid()))
            while True:
                time.sleep(1)
        yield from super().stream_generate(handle, messages, params, cancel)


def make_runtime(tmp_path, **kwargs):
    marker = tmp_path / "started"
    catalog = FakeBackend(
        name=BackendName.GGUF,
        models=[model(name) for name in ("stuck", "good", "waiting", "partial", "crash")],
    )
    backend = ProcessBackend(catalog, partial(StuckRuntime, marker), **kwargs)
    session = ModelSession(BackendRegistry([backend]))
    return backend, session, marker


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Operation did not finish")


def run_in_thread(work):
    errors = []

    def run():
        try:
            work()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, errors


def test_force_stop_kills_stuck_loader_and_allows_another_model(tmp_path):
    backend, session, marker = make_runtime(tmp_path)
    thread, errors = run_in_thread(lambda: session.load(model("stuck").ref))
    try:
        wait_until(marker.exists)
        pid = int(marker.read_text())
        assert session.status().loading == model("stuck").ref
        start = time.monotonic()
        session.request_stop()
        assert time.monotonic() - start < 0.1
        thread.join(3)
        assert not thread.is_alive()
        assert errors[0].code == "cancelled"
        assert all(child.pid != pid for child in multiprocessing.active_children())
        assert backend._process is None
        assert session.status().loaded is None
        assert not session.status().generating
        assert session.status().loading is None
        session.load(model("good").ref)
        assert list(session.generate([], GenerationParams(), threading.Event())) == [
            "Hello", " world"
        ]
        process = backend._process
        session.load(model("good").ref)
        assert backend._process is process
    finally:
        session.request_stop()
        thread.join(3)
        backend.close()


def test_load_timeout_terminates_worker_and_releases_lock(tmp_path):
    backend, session, _ = make_runtime(tmp_path, load_timeout=0.3)
    try:
        with pytest.raises(EngineError) as exc:
            session.load(model("stuck").ref)
        assert exc.value.code == "load_timeout"
        assert backend._process is None
        assert not session.status().generating
        backend._load_timeout = 5
        assert session.load(model("good").ref).ref == model("good").ref
    finally:
        backend.close()


def test_crashed_loader_reports_error_and_can_retry(tmp_path):
    backend, session, _ = make_runtime(tmp_path)
    try:
        with pytest.raises(EngineError, match="exited unexpectedly"):
            session.load(model("crash").ref)
        assert not session.status().generating
        session.load(model("good").ref)
    finally:
        backend.close()


def test_cancel_before_first_response_does_not_wait_for_backend(tmp_path):
    backend, session, marker = make_runtime(tmp_path)
    cancel = threading.Event()
    session.load(model("waiting").ref)
    thread, errors = run_in_thread(
        lambda: list(session.generate([ChatTurn("user", "hi")], GenerationParams(), cancel))
    )
    try:
        wait_until(marker.exists)
        cancel.set()
        thread.join(3)
        assert not thread.is_alive()
        assert errors == []
        assert backend._process is None
        assert session.status().loaded is None
        assert not session.status().generating
    finally:
        cancel.set()
        thread.join(3)
        backend.close()


def test_first_response_timeout_unloads_worker(tmp_path):
    backend, session, _ = make_runtime(tmp_path, first_token_timeout=0.2)
    try:
        session.load(model("waiting").ref)
        with pytest.raises(EngineError) as exc:
            list(session.generate([], GenerationParams(), threading.Event()))
        assert exc.value.code == "load_timeout"
        assert backend._process is None
        assert session.status().loaded is None
    finally:
        backend.close()


def test_stall_after_first_token_preserves_partial_answer_and_releases_model(tmp_path):
    backend, session, _ = make_runtime(tmp_path, token_timeout=0.2)
    with SqliteStore(tmp_path / "data.db") as store:
        library = LibraryService(store)
        errors = []
        chat = ChatService(library, session, on_error=lambda cid, error: errors.append(error))
        cid = library.create_conversation(model=model("partial").ref).summary.id
        try:
            chat.send(cid, "Read the document")
            chat._worker_thread.join(5)
            assert not chat._worker_thread.is_alive()
            assert errors[0].code == "load_timeout"
            assert library.get_conversation(cid).messages[-1].content == "Saved partial response"
            assert session.status().loaded is None
            assert not session.status().generating
            assert backend._process is None
        finally:
            backend.close()


def test_queued_cancel_does_not_start_a_process(tmp_path):
    backend, session, marker = make_runtime(tmp_path)
    store = SqliteStore(tmp_path / "data.db")
    chat = ChatService(LibraryService(store), session)
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(EngineError) as exc:
            chat.catalog_load(model("stuck").ref, cancel=cancel)
        assert exc.value.code == "cancelled"
        assert backend._process is None
        assert not marker.exists()
        assert not chat._generating
    finally:
        backend.close()
        store.close()


@pytest.mark.parametrize("name", ["stuck", "partial"])
def test_stopping_chat_preserves_user_and_any_partial_response(tmp_path, name):
    backend, session, marker = make_runtime(tmp_path)
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    cid = library.create_conversation(model=model(name).ref).summary.id
    done, errors = [], []
    chat = ChatService(
        library, session, on_done=lambda cid, **result: done.append(result),
        on_error=lambda cid, error: errors.append(error),
    )
    try:
        chat.send(cid, "Keep my message")
        wait_until(marker.exists)
        chat.stop(cid)
        chat._worker_thread.join(3)
        assert not chat._worker_thread.is_alive()
        assert errors == []
        assert done[0]["cancelled"]
        messages = library.get_conversation(cid).messages
        assert messages[0].content == "Keep my message"
        assert len(messages) == (2 if name == "partial" else 1)
        if name == "partial":
            assert messages[1].content == "Saved partial response"
        assert not chat._generating
    finally:
        chat.cancel_current()
        chat._worker_thread.join(3)
        backend.close()
        store.close()


def ollama_for_test(base_url):
    return OllamaBackend(httpx.Client(base_url=base_url, timeout=1, trust_env=False))


@pytest.mark.parametrize("phase", ["load", "first_response"])
def test_ollama_stop_closes_request_before_headers_and_unloads_model(phase):
    entered, disconnected, unloaded = threading.Event(), threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply({"models": [{"name": "test", "size": 1}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload.get("keep_alive") == 0:
                unloaded.set()
                self.reply({"done": True})
            elif (phase == "load" and self.path == "/api/generate") or (
                phase == "first_response" and self.path == "/api/chat"
            ):
                entered.set()
                self.connection.settimeout(5)
                try:
                    closed = self.connection.recv(1) == b""
                except ConnectionResetError:
                    # Windows resets the socket when the worker process is ended.
                    closed = True
                if closed:
                    disconnected.set()
            else:
                self.reply({"done": True})

        def reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    catalog = ollama_for_test(base_url)
    backend = ProcessBackend(catalog, partial(ollama_for_test, base_url))
    session = ModelSession(BackendRegistry([backend]))
    cancel = threading.Event()
    ref = ModelRef(BackendName.OLLAMA, "test")

    def work():
        session.load(ref, cancel=cancel)
        if phase == "first_response":
            list(session.generate([], GenerationParams(), cancel))

    thread, errors = run_in_thread(work)
    try:
        assert entered.wait(5)
        cancel.set()
        thread.join(3)
        assert not thread.is_alive()
        # Windows reports a closed connection to the server more slowly.
        assert disconnected.wait(5)
        assert unloaded.wait(5)
        assert backend._process is None
        assert not session.status().generating
        if phase == "load":
            assert len(errors) == 1 and errors[0].code == "cancelled"
        else:
            assert errors == []
    finally:
        cancel.set()
        thread.join(3)
        backend.close()
        catalog._client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(2)
