from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class BackendName(StrEnum):
    MLX = "mlx"
    OLLAMA = "ollama"
    GGUF = "gguf"


@dataclass(frozen=True, slots=True)
class ModelRef:
    backend: BackendName
    name: str

    @property
    def id(self) -> str:
        return f"{self.backend}/{self.name}"


@dataclass(frozen=True, slots=True)
class LocalModel:
    ref: ModelRef
    path: Path | None
    size_bytes: int
    modified_at: datetime | None = None
    details: dict[str, str] = field(default_factory=dict)
    available: bool = True
    unavailable_reason: str | None = None


@dataclass(frozen=True, slots=True)
class LoadOptions:
    """GGUF context window. Default 8192. Not per-send max_tokens."""

    n_ctx: int = 8192


@dataclass(frozen=True, slots=True)
class GenerationParams:
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2048

    @classmethod
    def preset(cls, name: str) -> GenerationParams:
        key = name.strip().lower()
        return {
            "precise": cls(0.2, 0.8, 2048),
            "balanced": cls(0.7, 0.9, 2048),
            "creative": cls(1.1, 0.98, 2048),
        }[key]


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: str  # "user" | "assistant" | "system"
    content: str
    tokens_per_sec: float | None = None  # stores chunks/s
    elapsed_s: float | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: int
    title: str
    model: ModelRef | None
    project_id: int | None
    message_count: int
    updated_at: datetime
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Conversation:
    summary: ConversationSummary
    system_prompt: str
    messages: tuple[ChatTurn, ...]


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    name: str
    instructions: str
    default_model: ModelRef | None
    created_at: datetime


class CancelToken(Protocol):
    def is_set(self) -> bool: ...


class LoadedHandle(Protocol):
    model: LocalModel


class InferenceBackend(Protocol):
    name: BackendName

    def is_available(self) -> tuple[bool, str | None]:
        """(ok, reason). Never raise for 'not installed' / 'daemon down'."""
        ...

    def list_models(self) -> list[LocalModel]:
        """Raise EngineError on unexpected failure. Empty list is valid."""
        ...

    def load(self, model: LocalModel, options: LoadOptions | None = None) -> LoadedHandle: ...

    def unload(self, handle: LoadedHandle) -> None: ...

    def stream_generate(
        self,
        handle: LoadedHandle,
        messages: list[ChatTurn],
        params: GenerationParams,
        cancel: CancelToken,
    ) -> Iterator[str]:
        """Yield text deltas. Honor cancel between tokens."""
        ...

    def delete(self, model: LocalModel) -> None: ...
