from __future__ import annotations

import hashlib
import importlib.util
import json
import plistlib
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
signer = load_script("sign_macos")


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


@pytest.mark.parametrize("assessment,required,exit_code", [
    ("accepted\nsource=Notarized Developer ID", True, 0),
    ("rejected\nsource=Unnotarized Developer ID", True, 1),
    ("rejected\nsource=Unnotarized Developer ID", False, 0),
])
def test_mac_download_records_gatekeeper_and_can_require_notarization(
        tmp_path, monkeypatch, assessment, required, exit_code):
    monkeypatch.setattr(checker.sys, "platform", "darwin")
    package = tmp_path / "Orchevian-1.0.0-macos-arm64.dmg"
    monkeypatch.setattr(checker, "checked_download", lambda *args: (package, "0" * 64))
    monkeypatch.setattr(checker, "check_package", lambda *args: {"ok": True})
    accepted = assessment.startswith("accepted")
    monkeypatch.setattr(checker.subprocess, "run", lambda command, **kwargs: SimpleNamespace(
        returncode=0 if accepted or command[0] != "spctl" else 3, stdout="", stderr=assessment))
    report = tmp_path / "report.json"
    flags = ["--require-notarized"] if required else []
    assert checker.main(["--downloads", str(tmp_path), "--report", str(report), *flags]) \
        == exit_code
    verdicts = json.loads(report.read_text())["gatekeeper"]
    assert verdicts["notarized"] is accepted
    assert verdicts["Orchevian.app"]["stapled"]
    assert verdicts[package.name]["accepted"] is accepted


def test_signs_libraries_then_frameworks_then_app(tmp_path, monkeypatch):
    app = tmp_path / "Orchevian.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Orchevian"}))
    macho = b"\xcf\xfa\xed\xfe" + bytes(12)
    (contents / "MacOS" / "Orchevian").write_bytes(macho)
    frameworks = contents / "Frameworks"
    qt = frameworks / "PySide6/Qt/lib/QtCore.framework/Versions/A"
    qt.mkdir(parents=True)
    (qt / "QtCore").write_bytes(macho)
    (frameworks / "lib-dynload").mkdir()
    (frameworks / "lib-dynload" / "_ssl.so").write_bytes(macho)
    (frameworks / "libpython3.13.dylib").write_bytes(macho)
    (frameworks / "base_library.zip").write_bytes(b"PK\x03\x04")
    (frameworks / "QtCore").symlink_to("PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore")
    calls = []
    monkeypatch.setattr(signer.subprocess, "run", lambda command, **kwargs: (
        calls.append(command), SimpleNamespace(returncode=0))[1])
    signer.sign_app(app, "IDENTITY")
    libraries, framework, bundle, verify = calls
    # Each real library once; symlinks, data, the framework's binary and the app's
    # own executable are signed with their bundles instead.
    assert sorted(Path(item).name for item in libraries if item.startswith(str(app))) \
        == ["_ssl.so", "libpython3.13.dylib"]
    assert framework[-1].endswith("QtCore.framework")
    assert bundle[-1] == str(app)
    assert {"--timestamp", "runtime"} <= set(bundle)
    assert verify[:2] == ["codesign", "--verify"]
