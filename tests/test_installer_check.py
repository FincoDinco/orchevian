from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = load_script("check_installer")
installers = load_script("installers")


def download(tmp_path):
    package = tmp_path / "Orchevian-0.1.0-linux-x64.AppImage"
    package.write_bytes(b"installer fixture")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    package.with_name(package.name + ".sha256").write_text(f"{digest}  {package.name}\n")
    return package, digest


def test_selects_and_checks_actual_installer_not_portable_archive(tmp_path):
    package, digest = download(tmp_path)
    (tmp_path / "Orchevian-0.1.0-linux-x64.tar.gz").write_bytes(b"archive")
    assert checker.checked_download(tmp_path, "linux") == (package, digest)


def test_checksum_mismatch_prevents_running_download(tmp_path):
    package, _ = download(tmp_path)
    package.write_bytes(b"corrupted download")
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        checker.checked_download(tmp_path, "linux")


def test_checksum_must_name_the_installer(tmp_path):
    package, digest = download(tmp_path)
    package.with_name(package.name + ".sha256").write_text(f"{digest}  different.AppImage\n")
    with pytest.raises(RuntimeError, match="Invalid checksum"):
        checker.checked_download(tmp_path, "linux")


def test_missing_or_ambiguous_installer_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match="found 0"):
        checker.checked_download(tmp_path, "linux")
    download(tmp_path)
    (tmp_path / "Orchevian-0.2.0-linux-x64.AppImage").write_bytes(b"another build")
    with pytest.raises(RuntimeError, match="found 2"):
        checker.checked_download(tmp_path, "linux")


@pytest.mark.parametrize("frozen,checks", [
    (False, ["bundled runtimes ['gguf'] (CPU)"]),
    (True, ["Qt workspace startup and shutdown"]),
])
def test_smoke_rejects_unfrozen_app_or_missing_runtimes(tmp_path, monkeypatch, frozen, checks):
    report = tmp_path / "smoke.json"
    monkeypatch.setattr(checker.sys, "platform", "linux")

    def run(*args, **kwargs):
        report.write_text(json.dumps({"ok": True, "frozen": frozen, "checks": checks}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checker.subprocess, "run", run)
    with pytest.raises(RuntimeError):
        checker.smoke(tmp_path / "AppRun", tmp_path, report)


def test_smoke_removes_host_python_and_model_overrides(tmp_path, monkeypatch):
    report = tmp_path / "smoke.json"
    monkeypatch.setattr(checker.sys, "platform", "linux")
    for variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "ORCHEVIAN_SMOKE_MODEL_DIR"):
        monkeypatch.setenv(variable, "must not reach installed app")

    def run(command, **kwargs):
        for variable in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "ORCHEVIAN_SMOKE_MODEL_DIR"):
            assert variable not in kwargs["env"]
        assert kwargs["cwd"] == tmp_path
        report.write_text(json.dumps({"ok": True, "frozen": True,
                                      "checks": ["bundled runtimes ['gguf'] (CPU)"]}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(checker.subprocess, "run", run)
    assert checker.smoke(tmp_path / "AppRun", tmp_path, report)["ok"]


def test_windows_uninstalls_even_if_smoke_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(checker.sys, "platform", "win32")
    commands = []
    monkeypatch.setattr(checker.subprocess, "run", lambda args, **kwargs: commands.append(args))

    def failed_smoke(*args):
        raise RuntimeError("broken installed app")

    monkeypatch.setattr(checker, "smoke", failed_smoke)
    with pytest.raises(RuntimeError, match="broken installed app"):
        checker.check_package(tmp_path / "setup.exe", tmp_path, tmp_path / "smoke.json")
    assert commands[0][0].endswith("setup.exe")
    assert commands[-1][0].endswith("unins000.exe")
    assert "/NOICONS" in commands[0]


def test_windows_requires_disposable_host_and_records_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(checker.sys, "platform", "win32")
    report = tmp_path / "report.json"
    assert checker.main(["--downloads", str(tmp_path), "--report", str(report)]) == 1
    result = json.loads(report.read_text())
    assert not result["ok"]
    assert "--disposable-host" in result["error"]


def test_missing_installer_tools_fail_instead_of_skipping(tmp_path, monkeypatch):
    monkeypatch.setattr(installers.shutil, "which", lambda _: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing"))
    monkeypatch.delenv("APPIMAGETOOL", raising=False)
    with pytest.raises(RuntimeError, match="Inno Setup"):
        installers.windows_installer(tmp_path, tmp_path, "0.1.0")
    with pytest.raises(RuntimeError, match="appimagetool"):
        installers.linux_appimage(tmp_path, tmp_path, "0.1.0")
