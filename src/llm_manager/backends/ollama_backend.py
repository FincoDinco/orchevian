import json
import subprocess
from collections.abc import Generator
from datetime import datetime

import httpx

from ..models import LocalModel

BASE_URL = "http://localhost:11434"


def _get(path: str) -> httpx.Response | None:
    try:
        return httpx.get(f"{BASE_URL}{path}", timeout=5.0)
    except Exception:
        return None


class OllamaBackend:
    name = "ollama"

    def is_available(self) -> bool:
        resp = _get("/api/tags")
        return resp is not None and resp.status_code == 200

    def list_models(self) -> list[LocalModel]:
        resp = _get("/api/tags")
        if resp is None or resp.status_code != 200:
            return []
        models = []
        for m in resp.json().get("models", []):
            modified = None
            if ts := m.get("modified_at"):
                try:
                    modified = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    pass
            details = m.get("details", {})
            models.append(LocalModel(
                name=m["name"],
                backend="ollama",
                path=None,
                size=m.get("size", 0),
                modified=modified,
                details={
                    "quantization": details.get("quantization_level", ""),
                    "context_length": details.get("context_length", ""),
                    "family": details.get("family", ""),
                    "parameter_size": details.get("parameter_size", ""),
                },
            ))
        return models

    def delete(self, model: LocalModel) -> None:
        httpx.delete(f"{BASE_URL}/api/delete", json={"name": model.name}, timeout=10.0)

    def pull(self, model_name: str) -> Generator[str, None, None]:
        with httpx.stream("POST", f"{BASE_URL}/api/pull", json={"name": model_name, "stream": True}, timeout=None) as resp:
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    status = data.get("status", "")
                    if "completed" in data and "total" in data:
                        yield f"{status}: {data['completed']}/{data['total']}"
                    else:
                        yield status

    def stream_chat(
        self,
        model: LocalModel,
        messages: list[dict],
        params: dict,
    ) -> Generator[str, None, None]:
        payload = {
            "model": model.name,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": params.get("temperature", 0.7),
                "top_p": params.get("top_p", 0.9),
                "num_predict": params.get("max_tokens", 2048),
            },
        }
        with httpx.stream("POST", f"{BASE_URL}/api/chat", json=payload, timeout=None) as resp:
            for line in resp.iter_lines():
                if line:
                    data = json.loads(line)
                    if msg := data.get("message", {}).get("content"):
                        yield msg
                    if data.get("done"):
                        break
