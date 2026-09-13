"""Friendly, editable model labels. Runtime references always remain unchanged."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable

from PySide6.QtCore import QObject, QSettings, Signal

from llm_engine.domain.models import ModelRef

BACKEND_TITLES = {"ollama": "Ollama", "mlx": "MLX", "gguf": "GGUF"}
BACKEND_ORDER = ("ollama", "gguf", "mlx")


def friendly_name(name: str) -> str:
    """Keep family, version and model size; omit publishers and file-format suffixes."""
    leaf = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    leaf = re.sub(r"\.gguf$", "", leaf, flags=re.I)
    leaf = re.sub(r"-\d{5}-of-\d{5}$", "", leaf, flags=re.I)
    leaf = re.sub(r"(?:^|[-_.:])(?:i?q\d(?:[_-][a-z0-9]+)*|\d+[-_]?bit|bf16|fp16|f16)$",
                  "", leaf, flags=re.I)
    leaf = re.sub(r"(?:^|[-_:])(?:gguf|mlx|instruct|latest)(?=$|[-_:])", " ", leaf, flags=re.I)
    leaf = re.sub(r"^meta[-_]", "", leaf, flags=re.I)
    size = re.search(r"(?:^|[-_: ])(\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?[bm])(?=$|[-_: ])",
                     leaf, flags=re.I)
    size_label = size[1].upper() if size else ""
    if size:
        leaf = leaf[:size.start()] + " " + leaf[size.end():]
    leaf = re.sub(r"[-_:]+", " ", leaf)
    leaf = re.sub(r"(?i)\b(qwen|llama|gemma|phi|mistral)(?=\d)", r"\1 ", leaf)
    words = []
    brands = {"deepseek": "DeepSeek", "qwen": "Qwen", "llama": "Llama", "gpt": "GPT"}
    for word in leaf.split():
        words.append(brands.get(word.lower(), word if not word.islower() else word.capitalize()))
    title = " ".join(words) or "Model"
    return f"{title} · {size_label}" if size_label else title


class ModelNames(QObject):
    changed = Signal(str)

    def __init__(self, settings: QSettings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if settings is None:
            from llm_manager_app.widgets.settings import make_settings

            settings = make_settings()
        self._settings = settings

    @staticmethod
    def _key(ref: ModelRef) -> str:
        return "model_names/" + hashlib.sha256(ref.id.encode()).hexdigest()

    def display(self, ref: ModelRef) -> str:
        return str(self._settings.value(self._key(ref), "") or "") or friendly_name(ref.name)

    def rename(self, ref: ModelRef, name: str) -> None:
        name = " ".join(name.split())
        if len(name) > 60:
            raise ValueError("Choose a name with 60 characters or fewer.")
        if name:
            self._settings.setValue(self._key(ref), name)
        else:
            self._settings.remove(self._key(ref))
        self._settings.sync()
        self.changed.emit(ref.id)

    def labels(self, refs: Iterable[ModelRef]) -> dict[str, str]:
        """Disambiguate equal names within a backend without exposing long paths."""
        groups: dict[tuple[str, str], list[ModelRef]] = defaultdict(list)
        for ref in set(refs):
            groups[(str(ref.backend), self.display(ref))].append(ref)
        reserved: dict[str, set[str]] = defaultdict(set)
        for backend, title in groups:
            reserved[backend].add(title)
        labels = {}
        for (backend, title), group in sorted(groups.items()):
            index = 1
            for ref in sorted(group, key=lambda item: item.id):
                label = title
                if len(group) > 1:
                    while f"{title} ({index})" in reserved[backend]:
                        index += 1
                    label = f"{title} ({index})"
                    reserved[backend].add(label)
                    index += 1
                labels[ref.id] = label
        return labels
