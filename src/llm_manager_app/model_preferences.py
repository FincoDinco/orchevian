"""Persist complete model references, independently of their display names."""

import json

from PySide6.QtCore import QSettings

from llm_engine.domain.models import BackendName, ModelRef

KEY_DEFAULT_MODEL = "models/default"


def default_model(settings: QSettings) -> ModelRef | None:
    try:
        value = json.loads(str(settings.value(KEY_DEFAULT_MODEL, "null")))
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            return None
        if not value["name"].strip():
            return None
        return ModelRef(BackendName(value["backend"]), value["name"])
    except (ValueError, KeyError, TypeError):
        return None


def save_default_model(settings: QSettings, ref: ModelRef | None) -> None:
    if ref is None:
        settings.remove(KEY_DEFAULT_MODEL)
    else:
        settings.setValue(KEY_DEFAULT_MODEL, json.dumps({"backend": ref.backend, "name": ref.name}))
    settings.sync()
