"""Ollama pulls through the local daemon, using the shared download queue."""

from __future__ import annotations

import json
import re
import threading
from contextlib import nullcontext

import httpx

from llm_engine.backends.ollama import OLLAMA_BASE_URL, _close_on_cancel
from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.services.discovery import RemoteModel
from llm_engine.services.downloads import DownloadChoice, DownloadPlan


class OllamaDownloadService:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    def prepare(self, model: RemoteModel, token="") -> DownloadPlan:
        name = model.repo_id.strip()
        if model.format != "ollama" or not re.fullmatch(
            r"(?:[\w-]+/)?[\w][\w.-]*(?::[\w][\w.-]*)?", name
        ):
            raise EngineError("download_failed", "Enter an Ollama model:tag, such as qwen3:4b.")
        if ":" not in name:
            name += ":latest"
        choice = DownloadChoice(name, ())
        return DownloadPlan(model, "", (choice,))

    def download(self, plan, choice, cancel, progress, token=""):
        if choice not in plan.choices or self.prepare(plan.model).choices != plan.choices:
            raise EngineError("download_failed", "Choose a valid Ollama tag.")
        connection = nullcontext(self._client) if self._client is not None else httpx.Client(
            base_url=OLLAMA_BASE_URL, timeout=httpx.Timeout(30, connect=5), trust_env=False,
        )
        try:
            if cancel.is_set():
                raise EngineError("cancelled", "Download cancelled.")
            with connection as client, client.stream(
                "POST", "/api/pull", json={"model": choice.name, "stream": True},
            ) as response:
                response.raise_for_status()
                stopped = threading.Event()
                watcher = threading.Thread(
                    target=_close_on_cancel, args=(response, cancel, stopped), daemon=True,
                )
                watcher.start()
                layers = {}
                try:
                    for line in response.iter_lines():
                        if cancel.is_set():
                            raise EngineError(
                                "cancelled", "Download cancelled; Ollama can resume it."
                            )
                        if not line:
                            continue
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            raise ValueError("Invalid progress")
                        if data.get("error"):
                            raise EngineError("download_failed", str(data["error"])[:500])
                        if data.get("digest"):
                            total = max(0, int(data.get("total", 0)))
                            done = min(total, max(0, int(data.get("completed", 0))))
                            layers[data["digest"]] = (done, total)
                        progress(sum(d for d, _ in layers.values()),
                                 sum(t for _, t in layers.values()),
                                 str(data.get("status", "Downloading")))
                        if data.get("status") == "success":
                            return ModelRef(BackendName.OLLAMA, choice.name)
                finally:
                    stopped.set()
                    watcher.join(1)
            if cancel.is_set():
                raise EngineError("cancelled", "Download cancelled; Ollama can resume it.")
            raise EngineError(
                "download_failed", "Ollama ended the download before confirming success."
            )
        except (httpx.HTTPError, httpx.StreamError, ValueError, TypeError) as exc:
            if cancel.is_set():
                raise EngineError("cancelled", "Download cancelled; Ollama can resume it.") from exc
            raise EngineError(
                "download_failed", "Could not download from Ollama. Check that Ollama is running "
                "and the model tag exists, then retry.",
            ) from exc
