"""Pinned Hugging Face downloads, verified before appearing in the local catalog."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import httpx

from llm_engine.domain.errors import EngineError
from llm_engine.domain.models import BackendName, ModelRef
from llm_engine.services.discovery import RemoteModel


@dataclass(frozen=True, slots=True)
class HubFile:
    name: str
    size: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadChoice:
    name: str
    files: tuple[HubFile, ...]

    @property
    def size_bytes(self) -> int:
        return sum(file.size for file in self.files)


@dataclass(frozen=True, slots=True)
class DownloadPlan:
    model: RemoteModel
    revision: str
    choices: tuple[DownloadChoice, ...]


def _safe_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(10)),
        *(f"LPT{i}" for i in range(10)),
    }
    if (
        not parts
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or any(
            part.startswith(".")
            or part.endswith((" ", "."))
            or any(char in part for char in ':*?"<>|')
            or part.split(".")[0].upper() in reserved
            for part in parts
        )
        or "/".join(parts) != value
    ):
        raise EngineError("download_failed", "The repository contains an unsupported file path.")
    return value


def _http_error(exc: httpx.HTTPError) -> EngineError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {401, 403}:
            return EngineError(
                "access_denied",
                "Accept access on the model’s Hugging Face page, then add a "
                "read token in Options and retry.",
            )
        if status == 429:
            return EngineError("download_failed", "Hugging Face is busy. Try again shortly.")
        if status == 404:
            return EngineError("download_failed", "This model or file is no longer available.")
    # Never expose signed URLs or Authorization headers in UI/logs.
    return EngineError("download_failed", "Download interrupted. Check your connection and retry.")


class DownloadService:
    def __init__(self, model_dir: Path, client: httpx.Client | None = None) -> None:
        self.model_dir = model_dir
        self._client = client

    def _connection(self):
        return (
            nullcontext(self._client)
            if self._client is not None
            else httpx.Client(
                timeout=httpx.Timeout(10, connect=10),
                follow_redirects=True,
            )
        )

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        token = token.strip() or os.environ.get("HF_TOKEN", "").strip()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def prepare(self, model: RemoteModel, token: str = "") -> DownloadPlan:
        repo = _safe_path(model.repo_id)
        if len(PurePosixPath(repo).parts) != 2 or model.format not in {"mlx", "gguf"}:
            raise EngineError("download_failed", "Invalid model repository or format.")
        try:
            with self._connection() as client:
                response = client.get(
                    f"https://huggingface.co/api/models/{quote(repo, safe='/')}/revision/main",
                    params={"blobs": "true"},
                    headers=self._headers(token),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise _http_error(exc) from exc
        except ValueError as exc:
            raise EngineError(
                "download_failed", "Invalid file information from Hugging Face."
            ) from exc
        revision = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{40,64}", revision):
            raise EngineError("download_failed", "Hugging Face did not provide a model revision.")
        siblings = data.get("siblings")
        if not isinstance(siblings, list):
            raise EngineError("download_failed", "Invalid file information from Hugging Face.")
        files = []
        for raw in siblings:
            if not isinstance(raw, dict) or not isinstance(raw.get("rfilename"), str):
                continue
            name = raw["rfilename"]
            if name.startswith("."):
                continue
            lower = name.lower()
            if model.format == "gguf":
                wanted = lower.endswith(".gguf") and not PurePosixPath(lower).name.startswith(
                    "mmproj"
                )
            else:
                wanted = lower.endswith(
                    (
                        ".safetensors",
                        ".npz",
                        ".json",
                        ".model",
                        ".tiktoken",
                        ".txt",
                        ".jinja",
                    )
                ) or PurePosixPath(lower).name in {"license", "readme.md"}
            if not wanted:
                continue
            _safe_path(name)
            size = raw.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise EngineError(
                    "download_failed", "A file has no download size. Try again later."
                )
            lfs = raw.get("lfs") or {}
            digest = lfs.get("sha256") if isinstance(lfs, dict) else None
            if digest is not None and (
                not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
            ):
                raise EngineError("download_failed", "Invalid file checksum from Hugging Face.")
            files.append(HubFile(name, size, digest))
        if len({file.name.casefold() for file in files}) != len(files):
            raise EngineError("download_failed", "Conflicting filenames in this repository.")
        choices = self._choices(files, model.format)
        if not choices:
            raise EngineError(
                "download_failed", f"No complete {model.format.upper()} downloads found."
            )
        return DownloadPlan(model, data["sha"], tuple(choices))

    @staticmethod
    def _choices(files: list[HubFile], format: str) -> list[DownloadChoice]:
        if format == "mlx":
            names = {file.name for file in files}
            if (
                "config.json" not in names
                or not any(
                    name.endswith((".safetensors", ".npz")) and "/" not in name for name in names
                )
                or not any(
                    name in names for name in ("tokenizer.json", "tokenizer.model", "vocab.json")
                )
            ):
                return []
            # One complete snapshot. Alternative weights in nested folders are not required.
            return [
                DownloadChoice(
                    "Complete MLX model",
                    tuple(
                        file
                        for file in files
                        if "/" not in file.name or file.name.startswith("chat_templates/")
                    ),
                )
            ]
        groups: dict[str, list[HubFile]] = {}
        choices = []
        for file in files:
            match = re.fullmatch(r"(.+)-(\d{5})-of-(\d{5})\.gguf", file.name, re.I)
            if match:
                groups.setdefault(match[1], []).append(file)
            else:
                choices.append(DownloadChoice(file.name, (file,)))
        for name, parts in groups.items():
            parts.sort(key=lambda file: file.name)
            expected = int(re.search(r"-of-(\d+)\.gguf$", parts[0].name, re.I)[1])
            if len(parts) == expected and all(
                file.name.lower() == f"{name}-{index:05d}-of-{expected:05d}.gguf".lower()
                for index, file in enumerate(parts, 1)
            ):
                choices.append(DownloadChoice(name + ".gguf", tuple(parts)))
        return sorted(
            choices,
            key=lambda choice: (
                "q4_k_m" not in choice.name.lower(),
                "q4" not in choice.name.lower(),
                choice.size_bytes,
            ),
        )

    def download(
        self,
        plan: DownloadPlan,
        choice: DownloadChoice,
        cancel: threading.Event,
        progress: Callable[[int, int, str], None],
        token: str = "",
    ) -> ModelRef:
        if choice not in plan.choices or not choice.files:
            raise EngineError("download_failed", "Select a valid download.")
        repo = _safe_path(plan.model.repo_id)
        if (
            len(PurePosixPath(repo).parts) != 2 or plan.model.format not in {"mlx", "gguf"}
            or not re.fullmatch(r"[a-fA-F0-9]{40,64}", plan.revision)
        ):
            raise EngineError("download_failed", "Invalid download plan.")
        root = (self.model_dir / plan.model.format).resolve()
        target = root / repo
        if plan.model.format == "gguf":
            key = hashlib.sha256(choice.name.encode()).hexdigest()[:12]
            target = target / key
        target = target.resolve()
        if not target.is_relative_to(root) or target == root:
            raise EngineError(
                "download_failed", "The model destination is outside the model folder."
            )
        if target.exists():
            raise EngineError("already_installed", "Already downloaded. Find it in Installed.")
        try:
            self._check_cancel(cancel)
            root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(root).free < choice.size_bytes + 256 * 1024**2:
                raise EngineError("disk_full", "Not enough disk space for this download.")
            with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as temp:
                stage = Path(temp)
                done = 0
                with self._connection() as client:
                    for file in choice.files:
                        self._check_cancel(cancel)
                        local = stage / _safe_path(file.name)
                        local.parent.mkdir(parents=True, exist_ok=True)
                        url = (
                            f"https://huggingface.co/{quote(repo, safe='/')}/resolve/"
                            f"{plan.revision}/{quote(file.name, safe='/')}"
                        )
                        progress(done, choice.size_bytes, file.name)
                        digest = hashlib.sha256()
                        written = 0
                        with client.stream("GET", url, headers=self._headers(token)) as response:
                            response.raise_for_status()
                            with local.open("xb") as output:
                                for chunk in response.iter_bytes(256 * 1024):
                                    self._check_cancel(cancel)
                                    written += len(chunk)
                                    if written > file.size:
                                        raise EngineError(
                                            "download_failed", "File size mismatch. Retry."
                                        )
                                    output.write(chunk)
                                    digest.update(chunk)
                                    done += len(chunk)
                                    progress(done, choice.size_bytes, file.name)
                        if written != file.size or (
                            file.sha256 and digest.hexdigest() != file.sha256.lower()
                        ):
                            raise EngineError("download_failed", "File verification failed. Retry.")
                self._check_cancel(cancel)
                if plan.model.format == "mlx":
                    self._verify_mlx(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise EngineError("already_installed", "This model is already downloaded.")
                stage.rename(target)
            relative = target.relative_to(root)
            if plan.model.format == "gguf":
                relative = (relative / choice.files[0].name).with_suffix("")
            return ModelRef(BackendName(plan.model.format), relative.as_posix())
        except httpx.HTTPError as exc:
            self._check_cancel(cancel)
            raise _http_error(exc) from exc
        except OSError as exc:
            raise EngineError(
                "download_failed",
                "Could not save the model. Check disk space and folder permissions.",
            ) from exc

    @staticmethod
    def _verify_mlx(stage: Path) -> None:
        try:
            config = json.loads((stage / "config.json").read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("Invalid model configuration")
            for index in stage.glob("*.safetensors.index.json"):
                metadata = json.loads(index.read_text(encoding="utf-8"))
                weights = metadata.get("weight_map") if isinstance(metadata, dict) else None
                if not isinstance(weights, dict) or not weights:
                    raise ValueError("Missing weight map")
                for name in weights.values():
                    if not isinstance(name, str) or not (stage / _safe_path(name)).is_file():
                        raise ValueError("Missing model shard")
        except (ValueError, OSError) as exc:
            raise EngineError(
                "download_failed", "The MLX model has invalid configuration or missing weights."
            ) from exc

    @staticmethod
    def _check_cancel(cancel: threading.Event) -> None:
        if cancel.is_set():
            raise EngineError("cancelled", "Download cancelled. Partial files removed.")
