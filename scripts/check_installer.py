"""Verify a downloaded installer using only Python's standard library.

Runs on a fresh hosted runner with no app Python packages or model runtimes installed.
Windows installs/uninstalls the app and must use a disposable host. Linux checks the
AppImage's extracted launcher because hosted runners do not provide FUSE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def checked_download(downloads: Path, system: str) -> tuple[Path, str]:
    pattern = {"darwin": "Orchevian-*-macos-*.dmg",
               "win32": "Orchevian-*-windows-*-setup.exe",
               "linux": "Orchevian-*-linux-*.AppImage"}[system]
    found = sorted(downloads.glob(pattern))
    if len(found) != 1:
        raise RuntimeError(f"Expected exactly one {pattern} in {downloads}; found {len(found)}")
    package = found[0].resolve()
    sidecar = package.with_name(package.name + ".sha256")
    checksum = sidecar.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", checksum)
    if match is None or match[2] != package.name:
        raise RuntimeError(f"Invalid checksum file: {sidecar.name}")
    # Not hashlib.file_digest: it needs Python 3.11, and Ubuntu 22.04's python3 is 3.10.
    hasher = hashlib.sha256()
    with package.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            hasher.update(block)
    actual = hasher.hexdigest()
    if actual != match[1].lower():
        raise RuntimeError(f"Checksum mismatch: {package.name}")
    return package, actual


def smoke(program: Path, work: Path, report: Path) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX",
                          "ORCHEVIAN_SMOKE_MODEL_DIR"}}
    env["QT_QPA_PLATFORM"] = "offscreen"
    report.unlink(missing_ok=True)
    result = subprocess.run([str(program), "--smoke-test", str(report)],
                            cwd=work, env=env, timeout=120, check=False)
    payload = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    if result.returncode or payload.get("ok") is not True or payload.get("frozen") is not True:
        raise RuntimeError(f"Installed app smoke check failed: {payload or result.returncode}")
    expected = ["gguf", "mlx"] if sys.platform == "darwin" and platform.machine() == "arm64" \
        else ["gguf"]
    runtime_check = f"bundled runtimes {expected!r} ("
    if not any(check.startswith(runtime_check) for check in payload.get("checks", [])):
        raise RuntimeError(f"Installed app did not verify required runtimes: {expected}")
    return payload


def check_package(package: Path, work: Path, report: Path) -> dict:
    if sys.platform == "darwin":
        mount = work / "Mounted Download"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint",
                        str(mount), str(package)], check=True, timeout=120)
        installed = work / "Installed App" / "Orchevian.app"
        try:
            installed.parent.mkdir()
            subprocess.run(["ditto", str(mount / "Orchevian.app"), str(installed)],
                           check=True, timeout=120)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=True, timeout=60)
        return smoke(installed / "Contents/MacOS/Orchevian", work, report)
    if sys.platform == "win32":
        installed = work / "Installed App"
        subprocess.run([str(package), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        "/SP-", "/NOICONS", "/MERGETASKS=!desktopicon", f"/DIR={installed}"],
                       check=True, timeout=180)
        try:
            return smoke(installed / "Orchevian.exe", work, report)
        finally:
            subprocess.run([str(installed / "unins000.exe"), "/VERYSILENT",
                            "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=120)
    package.chmod(package.stat().st_mode | 0o100)
    subprocess.run([str(package), "--appimage-extract"], cwd=work, check=True,
                   timeout=120, stdout=subprocess.DEVNULL)
    return smoke(work / "squashfs-root/AppRun", work, report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--downloads", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--disposable-host", action="store_true",
                        help="Confirm Windows installation is on a disposable test host")
    args = parser.parse_args(argv)
    report = args.report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    result = {"ok": False, "platform": sys.platform, "architecture": platform.machine()}
    try:
        if sys.platform == "win32" and not args.disposable_host:
            raise RuntimeError("Windows install verification requires --disposable-host")
        package, digest = checked_download(args.downloads, sys.platform)
        result.update(package=package.name, sha256=digest, checksum_verified=True)
        with TemporaryDirectory(prefix="orchevian installed check ") as temporary:
            work = Path(temporary)
            payload = check_package(package, work, work / "smoke.json")
        result.update(ok=True, smoke=payload, outside_checkout=True,
                      python_environment_removed=True)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
