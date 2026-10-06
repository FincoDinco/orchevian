"""Turn a verified desktop build into the download for its platform.

macOS: a .dmg that opens as an installer window: drag the app onto Applications.
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
# The disk image's Finder window, in points: the app on the left, an arrow, and the
# Applications folder to drag it onto. scripts/make_icons.py draws the background to fit.
# The height is the whole window: macOS 26 and later spend about 68 points on the title
# and tab bar, and the path and status bars, if someone shows them, about 55 more.
# The content fits in what is left either way.
DMG_WINDOW = (660, 440)
DMG_APP_AT = (170, 175)
DMG_APPLICATIONS_AT = (490, 175)
DMG_ICON_SIZE = 128
# Without those bars the window has spare room, so the background runs on below the
# content instead of leaving a white strip.
DMG_BACKGROUND_SPARE = 200


def arch() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)


def checksum(path: Path) -> None:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n",
                                                     encoding="utf-8")


def macos_dmg(app: Path, dist: Path, version: str, smoke, sign=None) -> Path:
    import dmgbuild  # In the packaging group on macOS only.

    app = app.resolve()  # dmgbuild changes directory while it works.
    target = dist.resolve() / f"Orchevian-{version}-macos-{arch()}.dmg"
    target.unlink(missing_ok=True)
    # Opens as one installer window: the app, an arrow, and Applications to drag it onto.
    # dmgbuild copies the app with ditto, keeping its symlinks and signature, and writes
    # the window layout without needing Finder, so it works on build servers.
    dmgbuild.build_dmg(str(target), "Orchevian", settings={
        "format": "UDZO",
        "files": [str(app)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(app / "Contents" / "Resources" / "orchevian.icns"),
        "background": str(ICONS / "dmg-background.png"),  # With its @2x for Retina.
        "window_rect": ((200, 120), DMG_WINDOW),
        "icon_size": DMG_ICON_SIZE,
        "text_size": 13,
        "icon_locations": {app.name: DMG_APP_AT, "Applications": DMG_APPLICATIONS_AT},
        "hide_extensions": [app.name],
    })
    if sign is not None:
        # Signing and stapling change the image, so they come before its checksum.
        sign(target)
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


def windows_installer(folder: Path, dist: Path, version: str) -> Path:
    compiler = shutil.which("iscc") or next(
        (str(p) for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6"
                          / "ISCC.exe",) if p.is_file()), None,
    )
    if compiler is None:
        raise RuntimeError("Inno Setup (iscc) is required to build the Windows installer.")
    name = f"Orchevian-{version}-windows-{arch()}-setup"
    subprocess.run([compiler, f"/DAppVersion={version}", f"/DSourceDir={folder}",
                    f"/DOutputDir={dist}", f"/DOutputName={name}",
                    f"/DIconFile={ICONS / 'orchevian.ico'}", f"/DLicenseFile={ROOT / 'LICENSE'}",
                    str(ROOT / "packaging" / "windows" / "orchevian.iss")], check=True)
    target = dist / f"{name}.exe"
    checksum(target)
    return target


def linux_appimage(folder: Path, dist: Path, version: str) -> Path:
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if tool is None:
        raise RuntimeError("appimagetool is required to build the Linux AppImage.")
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
