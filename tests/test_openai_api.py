from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from llm_engine.backends.fake import FakeBackend
from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.services.catalog import CatalogService
from llm_engine.services.chat import ChatService
from llm_engine.services.openai_api import MAX_BODY_BYTES, MAX_RESPONSE_TOKENS, ApiServerService
from llm_engine.services.session import ModelSession
from llm_engine.store.library import LibraryService
from llm_engine.store.sqlite import SqliteStore

REF = ModelRef(BackendName.OLLAMA, "fake")
BODY = {"model": REF.id, "messages": [{"role": "user", "content": "secret prompt"}]}


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


@pytest.fixture
def stack(tmp_path):
    fake = FakeBackend()
    registry = BackendRegistry([fake])
    session = ModelSession(registry)
    store = SqliteStore(tmp_path / "data.db")
    library = LibraryService(store)
    chat = ChatService(library, session)
    api = ApiServerService(chat, CatalogService(registry, session))
    yield api, chat, fake, library
    api.stop()
    registry.close()
    store.close()


def auth(api):
    return {"Authorization": f"Bearer {api.api_key}"}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_models_and_stateless_completion(stack):
    api, _chat, fake, library = stack
    with TestClient(api.app, base_url="http://127.0.0.1", headers=auth(api)) as client:
        assert client.get("/v1/models").json()["data"][0]["id"] == REF.id
        response = client.post("/v1/chat/completions", json=BODY)
        assert response.status_code == 200
        body = response.json()
        assert body["choices"][0]["message"]["content"] == "Hello world"
        assert body["object"] == "chat.completion"
        assert "usage" not in body
        assert library.list_conversations() == []
        assert len(fake.load_calls) == 1
        assert client.post("/v1/chat/completions", json=BODY).status_code == 200
        assert len(fake.load_calls) == 1
        assert "secret prompt" not in json.dumps(api.status())


@pytest.mark.parametrize(
    "updates",
    [
        {"model": "missing"},
        {"model": "ollama/missing"},
        {"messages": []},
        {"temperature": -1},
        {"max_tokens": True},
        {"tools": []},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
    ],
)
def test_invalid_requests_do_not_claim_or_persist(stack, updates):
    api, chat, _fake, library = stack
    with TestClient(api.app, base_url="http://127.0.0.1", headers=auth(api)) as client:
        response = client.post("/v1/chat/completions", json=BODY | updates)
        assert response.status_code in {400, 404}
        assert "error" in response.json()
        assert library.list_conversations() == []
        wait_for(lambda: not chat._generating)


def test_stream_framing_and_midstream_error(stack):
    api, _chat, fake, _library = stack
    with TestClient(api.app, base_url="http://127.0.0.1", headers=auth(api)) as client:
        result = client.post("/v1/chat/completions", json=BODY | {"stream": True})
        assert result.headers["content-type"].startswith("text/event-stream")
        events = [line[6:] for line in result.text.splitlines() if line.startswith("data: ")]
        assert events[-1] == "[DONE]"
        chunks = [json.loads(e) for e in events[:-1]]
        assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
        assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "Hello world"
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
        assert len({c["id"] for c in chunks}) == 1
        fake._fail_after = 1
        result = client.post("/v1/chat/completions", json=BODY | {"stream": True})
        assert '"error"' in result.text
        assert '"finish_reason": "stop"' not in result.text
        assert result.text.endswith("data: [DONE]\n\n")
        fake._fail_after = 0
        result = client.post("/v1/chat/completions", json=BODY | {"stream": True})
        assert result.status_code == 503
        assert "error" in result.json()


def test_busy_both_directions_and_stop_does_not_cancel_gui(stack):
    api, chat, fake, library = stack
    gate = threading.Event()
    fake._block_generate = gate
    cid = library.create_conversation(model=REF).summary.id
    chat.send(cid, "GUI prompt")
    wait_for(lambda: chat._session.status().generating)
    with TestClient(api.app, base_url="http://127.0.0.1", headers=auth(api)) as client:
        response = client.post("/v1/chat/completions", json=BODY)
        assert response.status_code == 429
        api.stop()
        assert not chat._cancel.is_set()
    gate.set()
    chat._worker_thread.join(3)
    port = free_port()
    api.start(port)
    gate.clear()
    with httpx.stream(
        "POST",
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json=BODY | {"stream": True},
        timeout=5,
        headers=auth(api),
    ) as response:
        next(response.iter_lines())
        with pytest.raises(EngineError, match="generation already"):
            chat.send(cid, "must not persist")
        assert library.get_conversation(cid).messages[-1].content == "Hello world"
    wait_for(lambda: not chat._generating)


def test_disconnect_shutdown_restart_and_bind_failure(stack):
    api, chat, fake, _library = stack
    port = free_port()
    state = api.start(port)
    assert state["host"] == "127.0.0.1" and state["running"]
    fake._block_generate = threading.Event()
    with httpx.stream(
        "POST",
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json=BODY | {"stream": True},
        timeout=5,
        headers=auth(api),
    ) as response:
        next(response.iter_lines())
    wait_for(lambda: not chat._generating)
    api.stop()
    assert not api.status()["running"]
    with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
        httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=1)
    assert api.start(port)["running"]
    api.stop()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", port))
        occupied.listen()
        with pytest.raises(EngineError) as error:
            api.start(port)
        assert error.value.code == "bind_failed"


def test_request_history_is_bounded_and_browser_origins_rejected(stack):
    api, _chat, _fake, _library = stack
    with TestClient(api.app, base_url="http://127.0.0.1", headers=auth(api)) as client:
        for _ in range(55):
            client.get("/v1/models")
        assert len(api.status()["recent_requests"]) == 50
        assert (
            client.post(
                "/v1/chat/completions", json=BODY, headers={"Origin": "https://example.com"}
            ).status_code
            == 403
        )
        assert client.get("/v1/models", headers={"Host": "evil.example"}).status_code == 400


def test_stop_cancels_active_stream_and_releases_session(stack):
    api, chat, fake, _library = stack
    fake._block_generate = threading.Event()
    port = free_port()
    api.start(port)
    with httpx.stream("POST", f"http://127.0.0.1:{port}/v1/chat/completions",
                      json=BODY | {"stream": True}, timeout=5, headers=auth(api)) as response:
        next(response.iter_lines())
        api.stop()
        assert not chat._generating
    assert not api.status()["running"]


@pytest.mark.parametrize("stream", [False, True])
def test_disconnect_during_load_cancels_before_first_token(stack, stream):
    api, chat, fake, _library = stack
    entered = threading.Event()

    def load_cancellable(model, options, cancel):
        entered.set()
        assert cancel.wait(5), "loading was not cancelled on disconnect"
        raise EngineError("cancelled", "loading stopped")

    fake.load_cancellable = load_cancellable
    port = free_port()
    api.start(port)
    body = json.dumps(BODY | {"stream": stream}).encode()
    with socket.create_connection(("127.0.0.1", port)) as client:
        client.sendall(
            f"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Authorization: Bearer {api.api_key}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        assert entered.wait(2)
    wait_for(lambda: not chat._generating)
    assert chat._session.status().loaded is None


def test_api_requires_its_key_and_bounds_requests(stack):
    api, _chat, _fake, _library = stack
    assert api.api_key.startswith("ov-") and len(api.api_key) > 40
    with TestClient(api.app, base_url="http://127.0.0.1") as client:
        assert client.get("/v1/models").status_code == 401
        wrong = {"Authorization": "Bearer ov-wrong"}
        assert client.get("/v1/models", headers=wrong).status_code == 401
        assert client.post("/v1/chat/completions", json=BODY, headers=wrong).status_code == 401
        assert client.get("/v1/models", headers=auth(api)).status_code == 200
        huge = "x" * (MAX_BODY_BYTES + 1)
        big = BODY | {"messages": [{"role": "user", "content": huge}]}
        assert client.post("/v1/chat/completions", json=big, headers=auth(api)).status_code == 413

        def chunks():
            yield b'{"model": "x", "messages": [{"role": "user", "content": "'
            for _ in range(5):
                yield b"x" * (1024 * 1024)
            yield b'"}]}'

        streamed = client.post("/v1/chat/completions", content=chunks(),
                               headers=auth(api) | {"Content-Type": "application/json"})
        assert streamed.status_code == 413
        too_long = BODY | {"max_tokens": MAX_RESPONSE_TOKENS + 1}
        assert client.post("/v1/chat/completions", json=too_long,
                           headers=auth(api)).status_code in {400, 422}
