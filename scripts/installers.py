"""Turn a verified desktop build into the download for its platform.

macOS: a .dmg with the app and an Applications shortcut to drag it onto.
Windows: an Inno Setup installer with Start menu and desktop shortcuts.
Linux: an AppImage, a single file that runs on most distributions.
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "packaging" / "icons"


def arch() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)


def checksum(path: Path) -> None:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n",
                                                     encoding="utf-8")


def macos_dmg(app: Path, dist: Path, version: str, smoke) -> Path:
    target = dist / f"Orchevian-{version}-macos-{arch()}.dmg"
    target.unlink(missing_ok=True)
    with TemporaryDirectory(prefix="orchevian-dmg-") as staging:
        stage = Path(staging)
        # ditto keeps the bundle's symlinks, permissions and extended attributes.
        subprocess.run(["ditto", str(app), str(stage / app.name)], check=True)
        (stage / "Applications").symlink_to("/Applications")
        subprocess.run(["hdiutil", "create", "-volname", "Orchevian", "-srcfolder", str(stage),
                        "-fs", "HFS+", "-format", "UDZO", "-ov", str(target)],
                       check=True, stdout=subprocess.DEVNULL)
    # Check the copy people will actually run: mount the image and launch it.
    with TemporaryDirectory(prefix="orchevian-dmg-mount-") as mount:
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount,
                        str(target)], check=True, stdout=subprocess.DEVNULL)
        try:
            smoke(Path(mount) / app.name / "Contents" / "MacOS" / "Orchevian")
        finally:
            subprocess.run(["hdiutil", "detach", mount], check=True, stdout=subprocess.DEVNULL)
    checksum(target)
    return target


def windows_installer(folder: Path, dist: Path, version: str) -> Path | None:
    compiler = shutil.which("iscc") or next(
        (str(p) for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6"
                          / "ISCC.exe",) if p.is_file()), None,
    )
    if compiler is None:
        print("Inno Setup (iscc) not found; skipping the Windows installer.")
        return None
    name = f"Orchevian-{version}-windows-{arch()}-setup"
    subprocess.run([compiler, f"/DAppVersion={version}", f"/DSourceDir={folder}",
                    f"/DOutputDir={dist}", f"/DOutputName={name}",
                    f"/DIconFile={ICONS / 'orchevian.ico'}", f"/DLicenseFile={ROOT / 'LICENSE'}",
                    str(ROOT / "packaging" / "windows" / "orchevian.iss")], check=True)
    target = dist / f"{name}.exe"
    checksum(target)
    return target


def linux_appimage(folder: Path, dist: Path, version: str) -> Path | None:
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if tool is None:
        print("appimagetool not found; skipping the AppImage.")
        return None
    target = dist / f"Orchevian-{version}-linux-{arch()}.AppImage"
    with TemporaryDirectory(prefix="orchevian-appdir-") as temporary:
        appdir = Path(temporary) / "Orchevian.AppDir"
        shutil.copytree(folder, appdir / "usr" / "lib" / "orchevian", symlinks=True)
        shutil.copy(ICONS / "orchevian-512.png", appdir / "orchevian.png")
        icons = appdir / "usr" / "share" / "icons" / "hicolor"
        for size in (48, 128, 256, 512):
            place = icons / f"{size}x{size}" / "apps"
            place.mkdir(parents=True)
            shutil.copy(ICONS / f"orchevian-{size}.png", place / "orchevian.png")
        desktop = (ROOT / "packaging" / "linux" / "orchevian.desktop").read_text()
        (appdir / "orchevian.desktop").write_text(desktop)
        run = appdir / "AppRun"
        run.write_text('#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\n'
                       'exec "$HERE/usr/lib/orchevian/Orchevian" "$@"\n')
        run.chmod(0o755)
        env = os.environ | {"ARCH": {"x64": "x86_64"}.get(arch(), arch())}
        subprocess.run([tool, "--no-appstream", str(appdir), str(target)], check=True, env=env)
    checksum(target)
    return target
