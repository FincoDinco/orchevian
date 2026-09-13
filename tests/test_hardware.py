import subprocess
from dataclasses import replace
from types import SimpleNamespace

import pytest

from llm_engine.hardware import (
    GIB,
    GPU,
    Hardware,
    _linux_gpus,
    _nvidia_gpus,
    detect_hardware,
)
from llm_engine.services.discovery import RemoteModel


@pytest.mark.parametrize("system,machine,format", [
    ("Windows", "AMD64", "gguf"), ("Windows", "ARM64", "gguf"),
    ("Linux", "x86_64", "gguf"), ("Linux", "aarch64", "gguf"),
    ("Darwin", "x86_64", "gguf"), ("Darwin", "arm64", "mlx"),
])
def test_detect_memory_on_each_platform(monkeypatch, system, machine, format):
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.machine", lambda: machine)
    monkeypatch.setattr("psutil.virtual_memory", lambda: SimpleNamespace(
        total=16 * GIB, available=6 * GIB,
    ))
    monkeypatch.setattr("psutil.cpu_count", lambda: 8)
    monkeypatch.setattr("llm_engine.hardware._nvidia_gpus", lambda: [])
    monkeypatch.setattr("llm_engine.hardware._linux_gpus", lambda: [])
    hardware = detect_hardware()
    assert hardware.memory_bytes == 16 * GIB
    assert hardware.available_bytes == 6 * GIB
    assert hardware.cpu_count == 8
    assert hardware.model_budget == int(6 * GIB * 0.8)
    assert hardware.recommended_format == format


def test_gpu_and_cpu_fits_are_separate_and_multi_gpu_memory_is_not_summed():
    gpu = GPU("RTX", "NVIDIA", 8 * GIB, 6 * GIB)
    hardware = Hardware("Windows", "AMD64", 64 * GIB, 40 * GIB, (gpu, gpu))
    assert hardware.gpu_budget == int(6 * GIB * 0.8)
    small = RemoteModel("a/small", "gguf", 1, 4 * GIB, "estimate")
    large = replace(small, estimated_bytes=16 * GIB)
    assert "GPU memory" in small.execution_hint(hardware)
    assert "system RAM" in large.execution_hint(hardware)
    small_host = replace(hardware, memory_bytes=8 * GIB, available_bytes=4 * GIB)
    assert large.fit(small_host) != "Likely fits"


def test_apple_memory_is_shared_and_current_memory_pressure_limits_fit():
    hardware = Hardware("Darwin", "arm64", 24 * GIB, 5 * GIB)
    assert hardware.gpu_budget == hardware.model_budget == 4 * GIB
    model = RemoteModel("a/large", "mlx", 1, 10 * GIB, "estimate")
    assert model.fit(hardware) == "Exceeds suggested memory budget"


def test_low_ram_cpu_machine_still_has_a_small_model_budget():
    hardware = Hardware("Linux", "aarch64", 4 * GIB)
    assert hardware.model_budget == int(4 * GIB * 0.7)
    assert hardware.recommended_format == "gguf"


def test_unknown_memory_override_and_known_memory_cap():
    hardware = Hardware("Windows", "ARM64", None, memory_override_bytes=3 * GIB)
    assert hardware.model_budget == 3 * GIB
    assert replace(hardware, memory_bytes=4 * GIB).model_budget < 3 * GIB
    assert replace(hardware, memory_bytes=4 * GIB, available_bytes=0).model_budget == 0


def test_nvidia_probe_parses_multiple_cards_and_unavailable_metrics(monkeypatch):
    def run(command):
        assert "--query-gpu=name,memory.total,memory.free" in command
        return "RTX 4090, 24576, 20000\nRTX 3060, 12288, 9000\nUnknown, N/A, N/A\nbad row\n"

    monkeypatch.setattr("llm_engine.hardware._run", run)
    gpus = _nvidia_gpus()
    assert len(gpus) == 3
    assert gpus[0].memory_bytes == 24 * GIB
    assert gpus[1].available_bytes == 9000 * 1024**2
    assert gpus[2].model_budget is None


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("probe", 3)])
def test_gpu_probe_failure_is_optional(monkeypatch, error):
    def fail(command):
        raise error

    monkeypatch.setattr("llm_engine.hardware._run", fail)
    assert _nvidia_gpus() == []


def test_linux_amd_vram_and_intel_unknown_memory(tmp_path):
    amd = tmp_path / "card0" / "device"
    amd.mkdir(parents=True)
    (amd / "vendor").write_text("0x1002\n")
    (amd / "mem_info_vram_total").write_text(str(16 * GIB))
    (amd / "mem_info_vram_used").write_text(str(2 * GIB))
    intel = tmp_path / "card1" / "device"
    intel.mkdir(parents=True)
    (intel / "vendor").write_text("0x8086\n")
    gpus = _linux_gpus(tmp_path)
    assert gpus[0].vendor == "AMD" and gpus[0].available_bytes == 14 * GIB
    assert gpus[1].vendor == "Intel" and gpus[1].memory_bytes is None
