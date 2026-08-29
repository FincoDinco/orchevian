"""OpenAI-compatible local API server."""
import json
import threading
import time
import uuid
from typing import Any

_server_thread: threading.Thread | None = None
_server_running = False
_active_model: Any = None
_active_backend: Any = None
_request_log: list[dict] = []


def get_status() -> dict:
    from .config import get_config
    cfg = get_config()
    return {
        "running": _server_running,
        "host": cfg.api_host,
        "port": cfg.api_port,
        "model": _active_model.name if _active_model else None,
        "backend": _active_backend.name if _active_backend else None,
    }


def get_request_log() -> list[dict]:
    return list(_request_log[-50:])  # last 50 requests


def set_active_model(model, backend) -> None:
    global _active_model, _active_backend
    _active_model = model
    _active_backend = backend


def start(host: str | None = None, port: int | None = None) -> None:
    global _server_thread, _server_running
    if _server_running:
        return

    from .config import get_config
    cfg = get_config()
    _host = host or cfg.api_host
    _port = port or cfg.api_port

    def _run():
        global _server_running
        import uvicorn
        _server_running = True
        uvicorn.run(_build_app(), host=_host, port=_port, log_level="error")
        _server_running = False

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()


def stop() -> None:
    # uvicorn doesn't have a clean programmatic stop; we rely on daemon thread
    global _server_running
    _server_running = False


def _build_app():
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import StreamingResponse
    from pydantic import BaseModel

    app = FastAPI(title="LLM Manager API", version="1.0.0")

    class ChatMessage(BaseModel):
        role: str
        content: str

    class ChatRequest(BaseModel):
        model: str = ""
        messages: list[ChatMessage]
        stream: bool = False
        temperature: float = 0.7
        top_p: float = 0.9
        max_tokens: int = 2048

    @app.get("/v1/models")
    def list_models():
        from .backends import list_all_models
        models = list_all_models()
        return {
            "object": "list",
            "data": [
                {"id": f"{m.backend}/{m.name}", "object": "model", "created": int(time.time())}
                for m in models
            ],
        }

    @app.post("/v1/chat/completions")
    def chat(req: ChatRequest):
        model = _active_model
        backend = _active_backend
        if model is None or backend is None:
            raise HTTPException(status_code=400, detail="No active model selected")

        messages = [{"role": m.role, "content": m.content} for m in req.messages]
        params = {"temperature": req.temperature, "top_p": req.top_p, "max_tokens": req.max_tokens}

        _request_log.append({
            "time": time.strftime("%H:%M:%S"),
            "model": model.name,
            "messages": len(messages),
            "stream": req.stream,
        })

        if req.stream:
            def _stream():
                cid = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                for token in backend.stream_chat(model, messages, params):
                    chunk = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model.name,
                        "choices": [{"delta": {"content": token}, "index": 0, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(_stream(), media_type="text/event-stream")

        content = "".join(backend.stream_chat(model, messages, params))
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model.name,
            "choices": [{"message": {"role": "assistant", "content": content}, "index": 0, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": -1, "completion_tokens": -1, "total_tokens": -1},
        }

    return app
