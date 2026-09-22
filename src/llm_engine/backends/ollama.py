"""Ollama backend. Talks to ``http://127.0.0.1:11434`` (never ``localhost``)."""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from typing import Any

import httpx

from llm_engine.backends.protocol import ModelHandle
from llm_engine.domain.errors import EngineError
from llm_engine.domain.images import validate_image_inputs
from llm_engine.domain.models import (
    BackendName,
    CancelToken,
    ChatTurn,
    GenerationParams,
    LoadedHandle,
    LoadOptions,
    LocalModel,
    ModelRef,
)

OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_BASE_URL = f"http://{OLLAMA_HOST}:{OLLAMA_PORT}"
_DOWN = f"Ollama is not running at {OLLAMA_HOST}:{OLLAMA_PORT}"


def _parse_modified(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def _str_details(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    details: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        details[str(key)] = str(value)
    return details


def _handle_options(handle: LoadedHandle) -> LoadOptions:
    options = getattr(handle, "options", None)
    if isinstance(options, LoadOptions):
        return options
    return LoadOptions()


def _close_on_cancel(response: httpx.Response, cancel: CancelToken, stop: threading.Event) -> None:
    while not stop.is_set():
        if cancel.is_set():
            try:
                response.close()
            except Exception:
                pass
            return
        stop.wait(0.05)


def _chunk_content(data: object) -> tuple[str | None, bool]:
    if not isinstance(data, dict):
        raise EngineError("load_failed", "Ollama returned invalid JSON")
    if err := data.get("error"):
        raise EngineError("load_failed", str(err))
    message = data.get("message")
    if message is None:
        content = None
    elif not isinstance(message, dict):
        raise EngineError("load_failed", "Ollama returned invalid JSON")
    else:
        raw = message.get("content")
        content = raw if isinstance(raw, str) and raw else None
    return content, bool(data.get("done"))


class OllamaBackend:
    name = BackendName.OLLAMA

    def __init__(self, client: httpx.Client | None = None) -> None:
        if client is None:
            self._client = httpx.Client(base_url=OLLAMA_BASE_URL, timeout=5.0, trust_env=False)
            self._owns_client = True
        else:
            self._client = client
            self._owns_client = False
        self._capabilities: dict[str, tuple[object, bool | None]] = {}

    def _supports_images(self, name, revision=None):
        cached = self._capabilities.get(name)
        if cached is not None and revision is not None and cached[0] == revision:
            return cached[1]
        try:
            response = self._client.post("/api/show", json={"model": name}, timeout=2.0)
            response.raise_for_status()
            payload = response.json()
            capabilities = payload.get("capabilities")
            result = "vision" in capabilities if isinstance(capabilities, list) else None
        except (httpx.HTTPError, ValueError, AttributeError):
            result = None
        if result is not None and revision is not None:
            self._capabilities[name] = (revision, result)
        return result

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
            self._owns_client = False

    def is_available(self) -> tuple[bool, str | None]:
        try:
            response = self._client.get("/api/tags")
        except httpx.RequestError as exc:
            return False, f"{_DOWN} ({exc})"
        if response.status_code == 200:
            return True, None
        return False, f"Ollama at {OLLAMA_HOST}:{OLLAMA_PORT} returned HTTP {response.status_code}"

    def list_models(self) -> list[LocalModel]:
        try:
            response = self._client.get("/api/tags")
        except httpx.RequestError as exc:
            raise EngineError("backend_unavailable", f"{_DOWN} ({exc})") from exc
        if response.status_code != 200:
            raise EngineError(
                "backend_unavailable",
                f"Ollama at {OLLAMA_HOST}:{OLLAMA_PORT} returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise EngineError("backend_unavailable", "Ollama returned invalid JSON") from exc
        raw_models = payload.get("models", []) if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise EngineError(
                "backend_unavailable",
                "Ollama /api/tags returned an unexpected payload",
            )
        models: list[LocalModel] = []
        for item in raw_models:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            size = item.get("size") or 0
            try:
                size_bytes = int(size)
            except (TypeError, ValueError):
                size_bytes = 0
            models.append(
                LocalModel(
                    ref=ModelRef(BackendName.OLLAMA, str(item["name"])),
                    path=None,
                    size_bytes=size_bytes,
                    modified_at=_parse_modified(item.get("modified_at")),
                    details=_str_details(item.get("details")),
                    supports_images=self._supports_images(str(item["name"]), item.get("digest")),
                )
            )
        return models

    def load(self, model: LocalModel, options: LoadOptions | None = None) -> ModelHandle:
        options = options or LoadOptions()
        if model.supports_images is None:
            model = replace(model, supports_images=self._supports_images(model.ref.name))
        self._model_request(
            {"model": model.ref.name, "stream": False, "options": {"num_ctx": options.n_ctx}},
            timeout=120.0,
        )
        return ModelHandle(model=model, options=options)

    def unload(self, handle: LoadedHandle) -> None:
        self.release_model(handle.model)

    def release_model(self, model: LocalModel) -> None:
        try:
            self._model_request(
                {"model": model.ref.name, "keep_alive": 0, "stream": False}, timeout=5.0
            )
        except EngineError as exc:
            raise EngineError(
                "stop_failed",
                "The request was stopped, but Ollama did not confirm unloading the model. "
                f"Try `ollama stop {model.ref.name}` or restart Ollama if it is unresponsive.",
            ) from exc

    def _model_request(self, payload: dict[str, Any], *, timeout: float) -> None:
        try:
            response = self._client.post("/api/generate", json=payload, timeout=timeout)
            if response.status_code != 200:
                raise EngineError(
                    "load_failed", response.text or f"Ollama HTTP {response.status_code}"
                )
            data = response.json()
            if not isinstance(data, dict) or data.get("error") or data.get("done") is not True:
                raise EngineError("load_failed", "Ollama did not confirm the model operation.")
        except (httpx.RequestError, ValueError) as exc:
            raise EngineError("backend_unavailable", f"Ollama request failed: {exc}") from exc

    def stream_generate(
        self,
        handle: LoadedHandle,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        if cancel.is_set():
            return
        validate_image_inputs(messages)
        if any(turn.images for turn in messages):
            # Confirm actual runtime capability instead of inferring it from a model name.
            if self._supports_images(handle.model.ref.name) is not True:
                raise EngineError(
                    "vision_required",
                    "This Ollama model does not report vision "
                    "support. Select a vision model or use extracted text only.",
                )
        payload: dict[str, Any] = {
            "model": handle.model.ref.name,
            "messages": [
                dict(
                    role=turn.role,
                    content=turn.content,
                    **(
                        {"images": [base64.b64encode(data).decode("ascii") for data in turn.images]}
                        if turn.images
                        else {}
                    ),
                )
                for turn in messages
            ],
            "stream": True,
            "think": False,
            "options": {
                "temperature": params.temperature,
                "top_p": params.top_p,
                "num_predict": params.max_tokens,
                "num_ctx": _handle_options(handle).n_ctx,
            },
        }
        if cancel.is_set():
            return
        try:
            with self._client.stream("POST", "/api/chat", json=payload, timeout=None) as response:
                if cancel.is_set():
                    response.close()
                    return
                if response.status_code != 200:
                    body = response.read().decode("utf-8", errors="replace")
                    code = "not_found" if response.status_code == 404 else "load_failed"
                    raise EngineError(code, body or f"Ollama HTTP {response.status_code}")
                yield from self._iter_chat_stream(response, cancel)
        except httpx.RequestError as exc:
            if cancel.is_set():
                return
            raise EngineError("backend_unavailable", f"{_DOWN} ({exc})") from exc

    def _iter_chat_stream(self, response: httpx.Response, cancel: CancelToken) -> Iterator[str]:
        stop = threading.Event()
        watcher = threading.Thread(
            target=_close_on_cancel,
            args=(response, cancel, stop),
            daemon=True,
            name="ollama-cancel",
        )
        watcher.start()
        try:
            if cancel.is_set():
                response.close()
                return
            for line in response.iter_lines():
                if cancel.is_set():
                    response.close()
                    return
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EngineError("load_failed", "Ollama returned invalid JSON") from exc
                content, done = _chunk_content(data)
                if content:
                    yield content
                if done:
                    return
        except Exception as exc:
            if cancel.is_set():
                return
            if isinstance(exc, EngineError):
                raise
            if isinstance(exc, httpx.HTTPError | httpx.StreamError):
                raise EngineError("backend_unavailable", f"{_DOWN} ({exc})") from exc
            raise
        finally:
            stop.set()
            watcher.join(timeout=1.0)

    def delete(self, model: LocalModel) -> None:
        try:
            response = self._client.request(
                "DELETE",
                "/api/delete",
                json={"model": model.ref.name, "name": model.ref.name},
            )
        except httpx.RequestError as exc:
            raise EngineError("backend_unavailable", f"{_DOWN} ({exc})") from exc
        if response.status_code == 404:
            raise EngineError("not_found", f"Ollama model not found: {model.ref.name}")
        if response.status_code != 200:
            raise EngineError("load_failed", f"Ollama delete returned HTTP {response.status_code}")
