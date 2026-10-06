"""A plain Markdown vault. Files are the source of truth; links are derived on read."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from llm_engine.domain.errors import EngineError

WIKILINK = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")
_MAX_NOTE_BYTES = 512_000


ABOUT_ME = "about-me"  # Tag for notes about the user, recalled in every chat.


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prose(text: str) -> str:
    """Exclude fenced and inline code from link discovery."""
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text)
    return re.sub(r"`[^`\n]*`", "", text)


def link_target(value: str) -> str:
    return value.split("|", 1)[0].split("#", 1)[0].removesuffix(".md").strip()


def split_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """Read scalar properties without a YAML dependency; preserve the original on edits."""
    if not content.startswith("---\n"):
        return {}, content
    head, separator, body = content[4:].partition("\n---\n")
    if not separator:
        return {}, content
    properties: dict[str, object] = {}
    for line in head.splitlines():
        name, colon, value = line.partition(":")
        if colon and name and not name[0].isspace():
            try:
                properties[name] = json.loads(value.strip())
            except ValueError:
                properties[name] = value.strip().strip("\"'")
    return properties, body.lstrip("\n")


def markdown_note(title: str, body: str, **properties: object) -> str:
    metadata = {"title": title, "created": datetime.now(UTC).isoformat(), **properties}
    header = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items()
    )
    return f"---\n{header}\n---\n\n{body.strip()}\n"


def safe_stem(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\[\]#\x00-\x1f]', " ", title)
    name = " ".join(name.split()).strip(" .")[:100].rstrip(" .")
    if not name:
        raise EngineError("config_invalid", "Give the note a title.")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }:
        name = f"Note {name}"
    return name


@dataclass(frozen=True, slots=True)
class MemoryNote:
    key: str  # Vault-relative path without .md; stable wiki-link target.
    title: str
    content: str
    body: str
    links: tuple[str, ...]
    tags: tuple[str, ...]
    revision: str
    kind: str = "note"
    source_id: int | None = None


class MemoryVault:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self._lock = threading.RLock()

    def _path(self, key: str) -> Path:
        parts = key.replace("\\", "/").split("/")
        if not key or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
            raise EngineError("config_invalid", "Invalid note path.")
        path = (
            self.root.joinpath(*parts).with_suffix(".md")
            if key.endswith(".md")
            else (self.root.joinpath(*parts[:-1], parts[-1] + ".md"))
        )
        if not path.resolve().is_relative_to(self.root):
            raise EngineError("config_invalid", "Note must stay inside the vault.")
        current = path
        while current != self.root:
            if current.is_symlink():
                raise EngineError("config_invalid", "Linked files are not supported in the vault.")
            current = current.parent
        return path

    def read(self, key: str) -> MemoryNote:
        path = self._path(key)
        try:
            if path.stat().st_size > _MAX_NOTE_BYTES:
                raise EngineError("config_invalid", f"Note is too large: {key}")
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise EngineError("not_found", f"Note not found: {key}") from exc
        properties, body = split_frontmatter(content)
        heading = re.search(r"(?m)^# (.+)$", body)
        title = str(properties.get("title") or (heading[1] if heading else path.stem))
        tags = properties.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(tag) for tag in tags]
        tags.extend(re.findall(r"(?<!\w)#([\w/-]+)", prose(body)))
        source = properties.get("conversation_id")
        return MemoryNote(
            key=path.relative_to(self.root).as_posix().removesuffix(".md"),
            title=title,
            content=content,
            body=body,
            links=tuple(
                dict.fromkeys(
                    target
                    for match in WIKILINK.finditer(prose(body))
                    if (target := link_target(match[1]))
                )
            ),
            tags=tuple(dict.fromkeys(tags)),
            revision=digest(content),
            kind=str(properties.get("kind", "note")),
            source_id=source if isinstance(source, int) and not isinstance(source, bool) else None,
        )

    def list_notes(self, query: str = "") -> list[MemoryNote]:
        if not self.root.exists():
            return []
        notes: list[MemoryNote] = []
        words = query.casefold().split()
        for path in sorted(self.root.rglob("*.md")):
            relative = path.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                continue
            try:
                note = self.read(relative.as_posix().removesuffix(".md"))
            except (EngineError, UnicodeError, FileNotFoundError):
                continue
            haystack = f"{note.title}\n{note.body}\n{' '.join(note.tags)}".casefold()
            if all(word in haystack for word in words):
                notes.append(note)
        return sorted(notes, key=lambda note: (note.kind == "source", note.title.casefold()))

    @staticmethod
    def resolve(target: str, notes: list[MemoryNote], origin: str = "") -> MemoryNote | None:
        target = link_target(target).casefold()
        if not target:
            return None
        exact = [note for note in notes if note.key.casefold() == target]
        if exact:
            return exact[0]
        if origin:
            relative = (Path(origin).parent / target).as_posix().casefold()
            nearby = [note for note in notes if note.key.casefold() == relative]
            if nearby:
                return nearby[0]
        matches = [
            note
            for note in notes
            if (Path(note.key).name.casefold() == target or note.title.casefold() == target)
        ]
        return matches[0] if len(matches) == 1 else None

    def connections(self, notes: list[MemoryNote]) -> set[tuple[str, str]]:
        return {
            (note.key, found.key)
            for note in notes
            for target in note.links
            if (found := self.resolve(target, notes, note.key)) is not None
            and found.key != note.key
        }

    def create(self, title: str, body: str = "") -> MemoryNote:
        stem = safe_stem(title)
        with self._lock:
            keys = {note.key.casefold() for note in self.list_notes()}
            key = f"Notes/{stem}"
            number = 2
            while key.casefold() in keys:
                key = f"Notes/{stem} {number}"
                number += 1
            self.write_new({key: markdown_note(title.strip(), body or f"# {title.strip()}")})
            return self.read(key)

    def write_new(self, documents: dict[str, str]) -> None:
        """Publish complete files without overwriting existing notes, rolling back on failure."""
        with self._lock:
            paths = {key: self._path(key) for key in documents}
            written: list[Path] = []
            try:
                for key, content in documents.items():
                    path = paths[key]
                    self._check_size(content)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = self._stage(path, content)
                    try:
                        # Linking the staged file is atomic and fails if the target exists.
                        os.link(temporary, path)
                        written.append(path)
                    finally:
                        temporary.unlink(missing_ok=True)
            except Exception:
                for path in written:
                    path.unlink(missing_ok=True)
                raise

    @staticmethod
    def _check_size(content: str) -> None:
        if len(content.encode("utf-8")) > _MAX_NOTE_BYTES:
            raise EngineError("config_invalid", "Notes must be smaller than 512 KB.")

    @staticmethod
    def _stage(path: Path, content: str) -> Path:
        fd, name = tempfile.mkstemp(prefix=".note-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return temporary

    def save(self, key: str, content: str, *, revision: str) -> MemoryNote:
        self._check_size(content)
        with self._lock:
            current = self.read(key)
            if current.revision != revision:
                raise EngineError(
                    "conflict", "This note changed on disk. Copy your edits, then reload."
                )
            path = self._path(key)
            temporary = self._stage(path, content)
            try:
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return self.read(key)

    def trash(self, key: str, *, revision: str) -> None:
        with self._lock:
            if self.read(key).revision != revision:
                raise EngineError(
                    "conflict", "This note changed on disk. Reload before deleting it."
                )
            trash = self.root / ".trash"
            if trash.is_symlink():
                raise EngineError("config_invalid", "Trash must stay inside the vault.")
            trash.mkdir(exist_ok=True)
            self._path(key).rename(trash / f"{Path(key).name}-{uuid.uuid4().hex[:12]}.md")

    def recall(self, query: str, *, limit: int = 4, profile: int = 6) -> list[MemoryNote]:
        """Keyword matches, then up to ``profile`` notes tagged ``about-me``.

        Facts about the user (diet, tools, constraints) matter in chats that share no words
        with them, so the tag keeps them in context. Anyone can add or remove the tag.
        """
        notes = [note for note in self.list_notes() if note.kind != "source"]
        about = [note for note in notes if ABOUT_ME in note.tags][:profile]
        matches = self._matches(query, notes, limit)
        return matches + [note for note in about if note not in matches]

    @staticmethod
    def _matches(query: str, notes: list[MemoryNote], limit: int) -> list[MemoryNote]:
        terms = set(re.findall(r"\w{3,}", query.casefold())) - {
            "the",
            "and",
            "that",
            "this",
            "with",
            "from",
            "what",
            "have",
            "you",
            "for",
            "can",
            "how",
            "are",
            "about",
            "please",
            "tell",
            "would",
            "could",
        }
        if not terms:
            return []
        ranked = []
        for note in notes:
            title = set(re.findall(r"\w{3,}", note.title.casefold()))
            body = set(re.findall(r"\w{3,}", note.body.casefold()))
            score = 3 * len(title & terms) + len(body & terms)
            if score:
                ranked.append((score, note))
        ranked.sort(key=lambda item: (-item[0], item[1].key))
        return [note for _, note in ranked[:limit]]
