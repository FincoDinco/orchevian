"""Public Hub search and conservative, explicitly estimated memory recommendations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from llm_engine.domain.errors import EngineError
from llm_engine.hardware import GIB, Hardware, detect_hardware  # noqa: F401


def quantization_bits(name: str) -> int | None:
    """Read advertised precision from repository/file names; not a memory guarantee."""
    match = re.search(
        r"(?:^|[-_./:])(?:I?Q([2-8])(?:[_A-Z0-9]*)|([2-8]|16|32)[-_]?BIT|"
        r"(?:BF|FP|F)(16|32))(?=[-_.:/]|$)", name.upper(),
    )
    return int(next(value for value in match.groups() if value)) if match else None


@dataclass(frozen=True, slots=True)
class RemoteModel:
    repo_id: str
    format: str
    downloads: int
    estimated_bytes: int | None
    estimate_basis: str
    gated: bool = False

    @property
    def url(self) -> str:
        if self.format == "ollama":
            return "https://ollama.com/" + (
                "" if "/" in self.repo_id else "library/"
            ) + quote(self.repo_id.split(":")[0], safe="/") + "/tags"
        return "https://huggingface.co/" + quote(self.repo_id, safe="/")

    def fit(self, hardware: Hardware) -> str:
        if self.format == "mlx" and not hardware.apple_silicon:
            return "Requires Apple Silicon"
        if self.estimated_bytes is None:
            return "Memory fit unknown"
        budgets = [
            budget for budget in (hardware.model_budget, hardware.gpu_budget) if budget is not None
        ]
        if not budgets:
            return "Memory fit unknown"
        return (
            "Likely fits" if self.estimated_bytes <= max(budgets)
            else "Exceeds suggested memory budget"
        )

    def execution_hint(self, hardware: Hardware) -> str:
        if self.fit(hardware) != "Likely fits":
            return self.fit(hardware)
        if hardware.apple_silicon:
            return "Likely fits shared memory · Metal runtime required"
        if hardware.gpu_budget is not None and self.estimated_bytes <= hardware.gpu_budget:
            return "Likely fits GPU memory · compatible GPU runtime required"
        return "Likely fits system RAM · CPU or partial offload; may be slow"


def _estimate(repo_id: str, format: str) -> tuple[int | None, str]:
    # Names are hints, not verified model metadata. Use total MoE parameters,
    # including every expert, rather than an active-parameter suffix such as A3B.
    name = repo_id.rsplit("/", 1)[-1]
    match = re.search(
        r"(?:^|[-_])(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?([BM])(?:[-_]|$)", name, re.I,
    )
    if match is None:
        return None, "Parameter count unknown"
    scale = 1e9 if match[3].upper() == "B" else 1e6
    params = float(match[1]) * (float(match[2]) if match[2] else 1) * scale
    quant = re.search(r"(?:^|[-_])(?:I?Q([2-8])|([2-8])[-_]?bit)(?:[-_]|$)", name, re.I)
    bits = int(quant[1] or quant[2]) if quant else (4 if format == "gguf" else 16)
    # Include quantization metadata and a modest context/runtime allowance.
    size = int(params * bits / 8 * 1.2 + 2 * GIB)
    basis = f"Estimated from name at {bits}-bit, including 2 GB runtime/context allowance"
    if not quant and format == "gguf":
        basis += "; choose a Q4 file"
    return size, basis


class DiscoveryService:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client

    def search(self, query: str, format: str, *, limit: int = 60) -> list[RemoteModel]:
        if format not in {"mlx", "gguf"}:
            raise EngineError("config_invalid", "Choose MLX or GGUF.")
        params = {
            "search": query.strip(), "filter": format, "pipeline_tag": "text-generation",
            "sort": "downloads", "direction": "-1", "limit": str(limit), "full": "true",
        }
        try:
            if self._client is None:
                with httpx.Client(timeout=15, follow_redirects=True) as client:
                    response = client.get("https://huggingface.co/api/models", params=params)
            else:
                response = self._client.get("https://huggingface.co/api/models", params=params)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("Expected a model list")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            message = (
                "Hugging Face rate limit reached. Try again shortly."
                if code == 429 else f"Hugging Face search failed (HTTP {code}). Try again."
            )
            raise EngineError("search_failed", message) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise EngineError(
                "search_failed", "Could not search Hugging Face. Check your connection and retry."
            ) from exc
        models = []
        seen = set()
        for raw in data:
            if not isinstance(raw, dict):
                continue
            repo_id = raw.get("id")
            if not isinstance(repo_id, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo_id):
                continue
            if repo_id in seen:
                continue
            seen.add(repo_id)
            size, basis = _estimate(repo_id, format)
            downloads = raw.get("downloads", 0)
            models.append(RemoteModel(
                repo_id, format, downloads if isinstance(downloads, int) else 0,
                size, basis, bool(raw.get("gated")),
            ))
        return models

    def recommend(self, hardware: Hardware, format: str, query: str = "") -> list[RemoteModel]:
        if format == "mlx" and not hardware.apple_silicon:
            raise EngineError(
                "backend_unavailable", "MLX requires Apple Silicon. Choose GGUF in Options."
            )
        if hardware.model_budget is None and hardware.gpu_budget is None:
            raise EngineError(
                "hardware_unknown",
                "Memory could not be detected. Set a RAM budget with Options → Memory limit, "
                "or turn off Suggested for my computer.",
            )
        models = self.search(query, format, limit=100)
        fitting = [model for model in models if model.fit(hardware) == "Likely fits"]
        if hardware.gpu_budget is not None:
            fitting.sort(key=lambda model: model.estimated_bytes > hardware.gpu_budget)
        return fitting[:20]
