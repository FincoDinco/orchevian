"""Portable RAM detection shared by discovery and runtimes; no inference libraries or Qt."""

from __future__ import annotations

import csv
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

import psutil

GIB = 1024**3


def _budget(total: int | None, available: int | None, *, gpu: bool = False) -> int | None:
    if total is None or total <= 0:
        return None
    reserve = min(4 * GIB, max(GIB, total // 4))
    budget = int(total * 0.8) if gpu else min(int(total * 0.7), total - reserve)
    if available is not None:
        budget = min(budget, int(available * 0.8))
    return max(0, budget)


@dataclass(frozen=True, slots=True)
class GPU:
    name: str
    vendor: str
    memory_bytes: int | None = None
    available_bytes: int | None = None

    @property
    def model_budget(self) -> int | None:
        return _budget(self.memory_bytes, self.available_bytes, gpu=True)


@dataclass(frozen=True, slots=True)
class Hardware:
    system: str
    machine: str
    memory_bytes: int | None
    available_bytes: int | None = None
    gpus: tuple[GPU, ...] = ()
    cpu_count: int | None = None
    memory_override_bytes: int | None = None

    @property
    def apple_silicon(self) -> bool:
        return self.system == "Darwin" and self.machine.lower() in {"arm64", "aarch64"}

    @property
    def model_budget(self) -> int | None:
        budget = _budget(self.memory_bytes, self.available_bytes)
        if self.memory_override_bytes is not None:
            return (
                min(budget, self.memory_override_bytes)
                if budget is not None else self.memory_override_bytes
            )
        return budget

    @property
    def gpu_budget(self) -> int | None:
        # Apple memory is shared, not extra VRAM. Do not add it to system RAM.
        if self.apple_silicon:
            return self.model_budget
        budgets = [gpu.model_budget for gpu in self.gpus if gpu.model_budget is not None]
        # Multi-GPU tensor splitting is runtime-specific. Never sum cards here.
        return max(budgets) if budgets else None

    @property
    def recommended_format(self) -> str:
        return "mlx" if self.apple_silicon else "gguf"

    @property
    def summary(self) -> str:
        memory = (
            f"{self.memory_bytes / GIB:.0f} GB RAM"
            if self.memory_bytes is not None else "RAM could not be detected"
        )
        available = (
            f" · {self.available_bytes / GIB:.1f} GB available now"
            if self.available_bytes is not None else ""
        )
        cpu = f" · {self.cpu_count} logical CPUs" if self.cpu_count else ""
        budget = self.model_budget
        suffix = f" · {budget / GIB:.1f} GB RAM budget" if budget is not None else ""
        lines = [f"{self.system} {self.machine}{cpu} · {memory}{available}{suffix}"]
        if self.memory_override_bytes is not None:
            lines.append("Using your memory budget, capped by detected available RAM when known.")
        if self.apple_silicon:
            lines.append("Apple shared memory · MLX or GGUF with a Metal-enabled runtime.")
        else:
            lines.append(
                "GGUF can run on CPU; GPU acceleration requires a compatible runtime build."
            )
            for gpu in self.gpus:
                vram = (
                    f"{gpu.memory_bytes / GIB:.1f} GB VRAM"
                    if gpu.memory_bytes is not None else "VRAM unknown"
                )
                lines.append(f"{gpu.vendor}: {gpu.name} · {vram}")
            if not self.gpus:
                lines.append("GPU memory not detected; recommendations use system RAM.")
        return "\n".join(lines)


def _run(command: list[str]) -> str:
    kwargs = (
        {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        if platform.system() == "Windows" else {}
    )
    return subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=3, **kwargs,
    ).stdout


def _nvidia_gpus() -> list[GPU]:
    try:
        output = _run([
            "nvidia-smi", "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ])
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for row in csv.reader(output.splitlines()):
        if len(row) != 3:
            continue
        name, total, free = (value.strip() for value in row)
        try:
            memory = int(total) * 1024**2
            available = int(free) * 1024**2
            if memory <= 0 or not 0 <= available <= memory:
                continue
        except ValueError:
            # Some driver modes report N/A; keep the device but invent no VRAM.
            memory = available = None
        gpus.append(GPU(name, "NVIDIA", memory, available))
    return gpus


def _linux_gpus(root: Path = Path("/sys/class/drm")) -> list[GPU]:
    gpus = []
    for card in sorted(root.glob("card[0-9]*")):
        if not card.name.removeprefix("card").isdigit():
            continue
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text().strip()
            if vendor not in {"0x1002", "0x8086"}:
                continue
            name_path = device / "product_name"
            name = name_path.read_text().strip() if name_path.is_file() else card.name
            memory = available = None
            try:
                memory = int((device / "mem_info_vram_total").read_text())
                used = int((device / "mem_info_vram_used").read_text())
                if memory <= 0 or not 0 <= used <= memory:
                    memory = None
                else:
                    available = memory - used
            except (OSError, ValueError):
                memory = None
            gpus.append(GPU(name, "AMD" if vendor == "0x1002" else "Intel", memory, available))
        except OSError:
            continue
    return gpus


def detect_hardware() -> Hardware:
    system, machine = platform.system(), platform.machine()
    memory = available = count = None
    try:
        ram = psutil.virtual_memory()
        if ram.total > 0:
            memory = ram.total
            if 0 <= ram.available <= ram.total:
                available = ram.available
    except (OSError, ValueError, AttributeError, NotImplementedError, psutil.Error):
        pass
    try:
        count = psutil.cpu_count()
    except (OSError, NotImplementedError, psutil.Error):
        pass
    gpus = []
    if system in {"Windows", "Linux"}:
        gpus.extend(_nvidia_gpus())
    if system == "Linux":
        try:
            gpus.extend(_linux_gpus())
        except OSError:
            pass
    return Hardware(system, machine, memory, available, tuple(gpus), count)
