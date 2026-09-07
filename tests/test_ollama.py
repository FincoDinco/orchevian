from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from datetime import datetime

import httpx
import pytest

from llm_engine.backends.ollama import OLLAMA_BASE_URL, OllamaBackend
from llm_engine.backends.protocol import ModelHandle
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    GenerationParams,
    LoadOptions,
    LocalModel,
    ModelRef,
)

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_backend() -> Iterator[Callable[[Handler], OllamaBackend]]:
    clients: list[httpx.Client] = []

    def factory(handler: Handler) -> OllamaBackend:
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url=OLLAMA_BASE_URL,
            timeout=5.0,
            trust_env=False,
        )
        clients.append(client)
        return OllamaBackend(client=client)

    yield factory
    for client in clients:
        client.close()


def _model(name: str = "qwen3:8b") -> LocalModel:
    return LocalModel(ref=ModelRef(BackendName.OLLAMA, name), path=None, size_bytes=1)


def _generate(backend: OllamaBackend, cancel: threading.Event | None = None) -> list[str]:
    handle = ModelHandle(_model(), LoadOptions(n_ctx=8192))
    token = cancel if cancel is not None else threading.Event()
    return list(
        backend.stream_generate(
            handle,
            [ChatTurn(role="user", content="hi")],
            GenerationParams(),
            token,
        )
    )


def test_base_url_is_loopback_not_localhost() -> None:
    assert OLLAMA_BASE_URL == "http://127.0.0.1:11434"
    assert "localhost" not in OLLAMA_BASE_URL


def test_is_available_true(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 11434
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    ok, reason = make_backend(handler).is_available()
    assert ok is True
    assert reason is None


def test_is_available_false_when_daemon_down(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    ok, reason = make_backend(handler).is_available()
    assert ok is False
    assert reason is not None
    assert "127.0.0.1:11434" in reason
    assert "localhost" not in reason


def test_is_available_false_on_http_error(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    ok, reason = make_backend(handler).is_available()
    assert ok is False
    assert reason is not None
    assert "500" in reason


def test_list_models_parses_tags(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen3:8b",
                        "size": 4661224676,
                        "modified_at": "2026-08-28T11:39:57.313141Z",
                        "details": {
                            "family": "qwen3",
                            "parameter_size": "8B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                ]
            },
        )

    models = make_backend(handler).list_models()
    assert len(models) == 1
    model = models[0]
    assert model.ref.id == "ollama/qwen3:8b"
    assert model.path is None
    assert model.size_bytes == 4661224676
    assert model.modified_at == datetime(2026, 8, 28, 11, 39, 57, 313141)
    assert model.details["family"] == "qwen3"
    assert model.details["quantization_level"] == "Q4_K_M"


def test_list_models_empty_is_valid(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    assert make_backend(handler).list_models() == []


def test_list_models_down_raises(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    with pytest.raises(EngineError) as exc:
        make_backend(handler).list_models()
    assert exc.value.code == "backend_unavailable"
    assert "127.0.0.1:11434" in str(exc.value)


def test_list_models_http_error_raises(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    with pytest.raises(EngineError) as exc:
        make_backend(handler).list_models()
    assert exc.value.code == "backend_unavailable"


def test_load_preloads_and_unload_releases_model(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/generate"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    backend = make_backend(handler)
    model = _model()
    handle = backend.load(model, LoadOptions(n_ctx=4096))
    assert isinstance(handle, ModelHandle)
    assert handle.model is model
    assert handle.options.n_ctx == 4096
    backend.unload(handle)
    assert requests == [
        {"model": model.ref.name, "stream": False, "options": {"num_ctx": 4096}},
        {"model": model.ref.name, "stream": False, "keep_alive": 0},
    ]


@pytest.mark.parametrize("payload", [{"done": False}, {"error": "out of memory"}, []])
def test_load_requires_ollama_confirmation(make_backend, payload):
    backend = make_backend(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(EngineError):
        backend.load(_model())


def test_unresponsive_ollama_unload_is_bounded_and_reports_recovery(make_backend):
    def handler(request):
        assert request.extensions["timeout"]["read"] == 5.0
        raise httpx.ReadTimeout("stuck", request=request)

    backend = make_backend(handler)
    with pytest.raises(EngineError) as exc:
        backend.unload(ModelHandle(_model()))
    assert exc.value.code == "stop_failed"
    assert "did not confirm unloading" in str(exc.value)
    assert "ollama stop qwen3:8b" in str(exc.value)


def test_stream_generate_yields_deltas_and_sends_num_ctx(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        assert "localhost" not in str(request.url)
        captured["body"] = json.loads(request.content)
        body = (
            b'{"message":{"content":"Hel"},"done":false}\n'
            b'{"message":{"content":"lo"},"done":false}\n'
            b'{"message":{"content":""},"done":true}\n'
        )
        return httpx.Response(200, content=body)

    chunks = _generate(make_backend(handler))
    assert chunks == ["Hel", "lo"]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "qwen3:8b"
    assert body["stream"] is True
    assert body["think"] is False
    options = body["options"]
    assert isinstance(options, dict)
    assert options["num_ctx"] == 8192
    assert options["temperature"] == 0.7
    assert options["top_p"] == 0.9
    assert options["num_predict"] == 2048


def test_stream_generate_honors_cancel_and_closes_response(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    closed = threading.Event()
    cancel = threading.Event()

    def handler(_request: httpx.Request) -> httpx.Response:
        content = (
            b'{"message":{"content":"one"},"done":false}\n'
            b'{"message":{"content":"two"},"done":false}\n'
            b'{"done":true}\n'
        )
        response = httpx.Response(200, content=content)
        original = response.close

        def close() -> None:
            closed.set()
            original()

        response.close = close  # type: ignore[method-assign]
        return response

    backend = make_backend(handler)
    handle = ModelHandle(_model())
    chunks: list[str] = []
    for chunk in backend.stream_generate(
        handle, [ChatTurn(role="user", content="hi")], GenerationParams(), cancel
    ):
        chunks.append(chunk)
        cancel.set()
    assert chunks == ["one"]
    assert closed.is_set()


def test_stream_generate_cancel_closes_blocked_read(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    class HangAfterFirst(httpx.SyncByteStream):
        def __init__(self) -> None:
            self._gate = threading.Event()

        def __iter__(self) -> Iterator[bytes]:
            yield b'{"message":{"content":"one"},"done":false}\n'
            self._gate.wait(timeout=5.0)

        def close(self) -> None:
            self._gate.set()

    stream = HangAfterFirst()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    backend = make_backend(handler)
    handle = ModelHandle(_model())
    cancel = threading.Event()
    got_first = threading.Event()
    finished = threading.Event()
    chunks: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for chunk in backend.stream_generate(
                handle, [ChatTurn(role="user", content="hi")], GenerationParams(), cancel
            ):
                chunks.append(chunk)
                got_first.set()
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert got_first.wait(timeout=2)
    assert not finished.wait(timeout=0.15)
    cancel.set()
    assert finished.wait(timeout=2)
    thread.join(timeout=2)
    assert chunks == ["one"]
    assert errors == []


def test_stream_generate_non_dict_json_is_engine_error(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'["not", "an", "object"]\n')

    with pytest.raises(EngineError) as exc:
        _generate(make_backend(handler))
    assert exc.value.code == "load_failed"


def test_stream_generate_non_dict_message_is_engine_error(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"message":"oops","done":false}\n')

    with pytest.raises(EngineError) as exc:
        _generate(make_backend(handler))
    assert exc.value.code == "load_failed"


def test_owned_client_close_is_idempotent() -> None:
    backend = OllamaBackend()
    backend.close()
    backend.close()


def test_injected_client_close_does_not_close_client(
    make_backend: Callable[[Handler], OllamaBackend],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    backend = make_backend(handler)
    backend.close()
    ok, reason = backend.is_available()
    assert ok is True
    assert reason is None


def test_stream_generate_model_missing(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    with pytest.raises(EngineError) as exc:
        _generate(make_backend(handler))
    assert exc.value.code == "not_found"


def test_delete_calls_api(make_backend: Callable[[Handler], OllamaBackend]) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "ok"})

    make_backend(handler).delete(_model("qwen3:8b"))
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/api/delete"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["name"] == "qwen3:8b"
    assert body["model"] == "qwen3:8b"
