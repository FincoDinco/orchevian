from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class LocalModel:
    name: str
    backend: str  # "mlx" | "ollama" | "gguf"
    path: Path | None
    size: int  # bytes
    modified: datetime | None = None
    details: dict = field(default_factory=dict)  # quantization, context_length, etc.

    @property
    def display_backend(self) -> str:
        return {"mlx": "MLX", "ollama": "Ollama", "gguf": "GGUF"}.get(self.backend, self.backend.upper())


@dataclass
class ChatMessage:
    role: str  # "user" | "assistant" | "system"
    content: str
    tokens_per_sec: float | None = None
    elapsed: float | None = None


@dataclass
class Conversation:
    id: int
    title: str
    model_name: str
    backend: str
    system_prompt: str
    messages: list[ChatMessage] = field(default_factory=list)
    project_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class Project:
    """A folder of chats that also acts as a custom assistant: its instructions and
    default model seed every new chat created inside it."""

    id: int
    name: str
    instructions: str = ""
    model_name: str = ""
    backend: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class PromptTemplate:
    id: int
    name: str
    description: str
    system_prompt: str
    user_prompt: str
    created_at: datetime = field(default_factory=datetime.now)
