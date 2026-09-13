"""Orchevian engine library. No GUI toolkit imports."""

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import (
    BackendName,
    ChatTurn,
    Conversation,
    ConversationSummary,
    GenerationParams,
    LoadOptions,
    LocalModel,
    ModelRef,
    Project,
)

__version__ = "0.1.0"

__all__ = [
    "BackendName",
    "ChatTurn",
    "Conversation",
    "ConversationSummary",
    "EngineError",
    "GenerationParams",
    "LoadOptions",
    "LocalModel",
    "ModelRef",
    "Project",
    "__version__",
]
