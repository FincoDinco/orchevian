"""Catalog façade over the backend registry and model session.

``load`` / ``unload`` block on the session lock; the GUI must call them on a worker.
"""

from __future__ import annotations

from llm_engine.backends.registry import BackendRegistry
from llm_engine.domain.models import LoadOptions, LocalModel, ModelRef
from llm_engine.services.session import ModelSession, SessionStatus


class CatalogService:
    def __init__(self, registry: BackendRegistry, session: ModelSession) -> None:
        self._registry = registry
        self._session = session

    def list_models(self) -> tuple[list[LocalModel], dict[str, tuple[bool, str | None]]]:
        return self._registry.list_models()

    def load(self, ref: ModelRef, options: LoadOptions | None = None) -> LocalModel:
        return self._session.load(ref, options)

    def unload(self) -> None:
        self._session.unload()

    def delete(self, ref: ModelRef) -> None:
        self._session.delete(ref)

    def status(self) -> SessionStatus:
        return self._session.status()
