"""Catalog size totals and free space; never traverse or modify model files."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from llm_engine import config
from llm_engine.domain.models import BackendName, LocalModel


@dataclass(frozen=True, slots=True)
class BackendStorage:
    backend: str
    model_count: int
    size_bytes: int
    available: bool


@dataclass(frozen=True, slots=True)
class StorageSummary:
    backends: tuple[BackendStorage, ...]
    model_dir: Path
    free_bytes: int | None
    disk_error: str | None = None


def summary(
    models: list[LocalModel],
    availability: dict[str, tuple[bool, str | None]],
    *,
    model_dir: Path | None = None,
) -> StorageSummary:
    """Reported model sizes are logical totals, not unique allocated disk bytes.

    Ollama tags may share blobs. Local runtimes may expose an offline catalog.
    An unavailable backend with no rows is unknown, not proof of an empty library.
    """
    directory = Path(model_dir) if model_dir is not None else config.load().model_dir
    unique = {model.ref.id: model for model in models}
    names = sorted(
        {str(name) for name in BackendName}
        | set(availability)
        | {str(model.ref.backend) for model in models}
    )
    totals = []
    for name in names:
        rows = [model for model in unique.values() if str(model.ref.backend) == name]
        totals.append(
            BackendStorage(
                name,
                len(rows),
                sum(max(0, model.size_bytes) for model in rows),
                availability.get(name, (False, None))[0],
            )
        )
    try:
        probe = directory.expanduser().absolute()
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free = shutil.disk_usage(probe).free
        error = None
    except OSError as exc:
        free = None
        error = str(exc)
    return StorageSummary(tuple(totals), directory, free, error)
