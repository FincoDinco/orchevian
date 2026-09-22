"""Opt-in loopback HTTP API sharing ChatService's single inference session."""

from __future__ import annotations

import asyncio
import json
import queue
import socket
import threading
import time
import uuid
from collections import deque
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from llm_engine import config
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ChatTurn, GenerationParams, ModelRef
from llm_engine.services.catalog import CatalogService
from llm_engine.services.chat import ChatService


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1)
    messages: list[Message] = Field(min_length=1)
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    top_p: float = Field(default=0.9, gt=0, le=1, allow_inf_nan=False)
    max_tokens: int = Field(default=2048, ge=1)
    n: Literal[1] = 1


def parse_model_id(value: str) -> ModelRef:
    backend, separator, name = value.partition("/")
    try:
        if not separator or not name.strip():
            raise ValueError
        return ModelRef(BackendName(backend), name)
    except ValueError as exc:
        raise EngineError(
            "not_found", "Use a model ID returned by /v1/models (backend/name)."
        ) from exc


def error_body(code: str, message: str) -> dict:
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error"
            if code
            in {
                "config_invalid",
                "not_found",
                "no_model",
            }
            else "server_error",
            "param": None,
            "code": code,
        }
    }


def error_response(exc: EngineError) -> JSONResponse:
    status = {
        "generating": 429,
        "not_found": 404,
        "no_model": 400,
        "config_invalid": 400,
        "cancelled": 503,
    }.get(exc.code, 503)
    return JSONResponse(error_body(exc.code, str(exc)), status_code=status)


class _Generation:
    """Bounded producer queue keeps blocking model I/O off the ASGI event loop."""

    def __init__(self, chat: ChatService, req: CompletionRequest, ref: ModelRef) -> None:
        self.chat = chat
        self.cancel = threading.Event()
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=32)
        self.thread = threading.Thread(
            target=self._run, args=(req, ref), daemon=True, name="api-generate"
        )

    def _put(self, kind: str, value: object = None) -> None:
        while not self.cancel.is_set():
            try:
                self.queue.put((kind, value), timeout=0.05)
                return
            except queue.Full:
                pass

    def _run(self, req: CompletionRequest, ref: ModelRef) -> None:
        stream = self.chat.stream_external(
            ref,
            [ChatTurn(m.role, m.content) for m in req.messages],
            GenerationParams(req.temperature, req.top_p, req.max_tokens),
            self.cancel,
        )
        try:
            for token in stream:
                if self.cancel.is_set():
                    break
                self._put("token", token)
            self._put("done")
        except EngineError as exc:
            self._put("error", exc)
        except Exception:
            self._put("error", EngineError("backend_unavailable", "Model generation failed."))
        finally:
            stream.close()

    async def next(self, request: Request) -> tuple[str, object]:
        while True:
            if self.cancel.is_set() or await request.is_disconnected():
                raise EngineError("cancelled", "Model request stopped.")
            try:
                return self.queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02)

    def stop(self) -> None:
        self.chat.cancel_external(self.cancel)


class _CompletionStream(StreamingResponse):
    """Release inference even when sending headers/body fails on a disconnect."""

    def __init__(self, content, on_close) -> None:
        super().__init__(content, media_type="text/event-stream",
                         headers={"Cache-Control": "no-cache"})
        self._on_close = on_close

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_close()


class _RequestSummary:
    """Observe response headers without wrapping the request's disconnect channel."""

    def __init__(self, app, owner):
        self.app = app
        self.owner = owner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()

        async def record_send(message):
            if message["type"] == "http.response.start":
                with self.owner._lock:
                    self.owner._recent.append({
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": message["status"],
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                    })
            await send(message)

        if Request(scope).headers.get("origin"):
            response = JSONResponse(
                error_body("config_invalid", "Browser origins are disabled."), status_code=403
            )
            await response(scope, receive, record_send)
        else:
            await self.app(scope, receive, record_send)


class ApiServerService:
    def __init__(self, chat: ChatService, catalog: CatalogService, *, port: int = 8080) -> None:
        self._chat = chat
        self._catalog = catalog
        self._port = port
        self._lock = threading.Lock()
        self._lifecycle = threading.Lock()
        self._recent: deque[dict] = deque(maxlen=50)
        self._active: set[_Generation] = set()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._stopping = False
        self.app = self._build_app()

    def status(self) -> dict:
        with self._lock:
            return {
                "running": bool(
                    self._server
                    and self._server.started
                    and self._thread
                    and self._thread.is_alive()
                    and not self._stopping
                ),
                "host": config.API_HOST,
                "port": self._port,
                "url": f"http://{config.API_HOST}:{self._port}/v1",
                "active_requests": len(self._active),
                "recent_requests": list(self._recent),
            }

    def start(self, port: int | None = None) -> dict:
        with self._lifecycle:
            with self._lock:
                if self._stopping and self._active:
                    raise EngineError("generating", "Wait for the previous API request to stop.")
            if self._thread is not None and self._thread.is_alive():
                if self._stopping:
                    raise EngineError("generating", "Wait for the API to finish stopping.")
                if port is not None and port != self._port:
                    raise EngineError("config_invalid", "Stop the API before changing its port.")
                return self.status()
            port = self._port if port is None else port
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise EngineError("config_invalid", "Port must be between 1 and 65535.")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((config.API_HOST, port))
                sock.listen(128)
            except OSError as exc:
                sock.close()
                raise EngineError(
                    "bind_failed", f"Could not start API on port {port}: {exc}"
                ) from exc
            self._port = port
            self._socket = sock
            self._stopping = False
            server = uvicorn.Server(
                uvicorn.Config(
                    self.app,
                    host=config.API_HOST,
                    port=port,
                    access_log=False,
                    log_config=None,
                    lifespan="off",
                    timeout_graceful_shutdown=2,
                )
            )
            self._server = server
            self._thread = threading.Thread(
                target=server.run, kwargs={"sockets": [sock]}, daemon=True, name="local-api"
            )
            self._thread.start()
            deadline = time.monotonic() + 5
            while not server.started and self._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not server.started:
                server.should_exit = True
                self._thread.join(3)
                sock.close()
                raise EngineError("bind_failed", "API server did not start.")
            return self.status()

    def stop(self) -> None:
        with self._lifecycle:
            with self._lock:
                self._stopping = True
                active = list(self._active)
            for job in active:
                job.stop()
            if self._server is not None:
                self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(5)
                if self._thread.is_alive():
                    raise EngineError("backend_unavailable", "API is still stopping. Try again.")
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            deadline = time.monotonic() + 3
            for job in active:
                job.thread.join(max(0, deadline - time.monotonic()))
            if any(job.thread.is_alive() for job in active):
                raise EngineError("backend_unavailable", "API model request is still stopping.")

    def _finish(self, job: _Generation) -> None:
        job.stop()

        # Keep cancelled workers visible to stop() until model cleanup completes.
        def reap() -> None:
            job.thread.join()
            with self._lock:
                self._active.discard(job)

        threading.Thread(target=reap, daemon=True, name="api-cleanup").start()

    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

        app.add_middleware(_RequestSummary, owner=self)

        @app.exception_handler(RequestValidationError)
        async def invalid(_request, exc):
            errors = "; ".join(
                f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            return error_response(EngineError("config_invalid", errors))

        @app.get("/v1/models")
        def models():
            rows, _availability = self._catalog.list_models()
            return {
                "object": "list",
                "data": [
                    {
                        "id": m.ref.id,
                        "object": "model",
                        "created": 0,
                        "owned_by": str(m.ref.backend),
                    }
                    for m in rows
                    if m.available
                ],
            }

        @app.post("/v1/chat/completions")
        async def complete(req: CompletionRequest, request: Request):
            try:
                ref = parse_model_id(req.model)
            except EngineError as exc:
                return error_response(exc)
            job = _Generation(self._chat, req, ref)
            with self._lock:
                if self._stopping:
                    return error_response(EngineError("cancelled", "API is stopping."))
                self._active.add(job)
                job.thread.start()
            handed_off = False
            try:
                first = await job.next(request)
                if first[0] == "error":
                    return error_response(first[1])
                envelope = {
                    "id": f"chatcmpl-{uuid.uuid4().hex}",
                    "created": int(time.time()),
                    "model": ref.id,
                }
                if req.stream:

                    async def events():
                        def chunk(delta, finish=None):
                            data = dict(
                                envelope,
                                object="chat.completion.chunk",
                                choices=[
                                    {
                                        "index": 0,
                                        "delta": delta,
                                        "finish_reason": finish,
                                    }
                                ],
                            )
                            return "data: " + json.dumps(data) + "\n\n"

                        try:
                            yield chunk({"role": "assistant", "content": ""})
                            kind, value = first
                            while kind == "token":
                                yield chunk({"content": value})
                                kind, value = await job.next(request)
                            if kind == "error":
                                yield (
                                    "data: "
                                    + json.dumps(error_body(value.code, str(value)))
                                    + "\n\n"
                                )
                            else:
                                yield chunk({}, "stop")
                            yield "data: [DONE]\n\n"
                        except EngineError as exc:
                            yield "data: " + json.dumps(error_body(exc.code, str(exc))) + "\n\n"
                            yield "data: [DONE]\n\n"

                    handed_off = True
                    return _CompletionStream(events(), lambda: self._finish(job))
                parts = []
                kind, value = first
                while kind == "token":
                    parts.append(value)
                    kind, value = await job.next(request)
                if kind == "error":
                    return error_response(value)
                return dict(
                    envelope,
                    object="chat.completion",
                    choices=[
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "".join(parts)},
                            "finish_reason": "stop",
                        }
                    ],
                )
            except EngineError as exc:
                return error_response(exc)
            finally:
                if not handed_off:
                    self._finish(job)

        return app
