"""Build, smoke-test, and archive the standalone desktop app on the current OS."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import installers  # noqa: E402
import sign_macos  # noqa: E402


def compile_macos_icon() -> None:
    """Compile the Icon Composer icon the spec bundles: Assets.car, which macOS 26 and
    later draw as Liquid Glass, and a flattened .icns for earlier versions."""
    out = ROOT / "build" / "macos-icon"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["xcrun", "actool", str(ROOT / "packaging" / "icons" / "orchevian.icon"),
         "--compile", str(out), "--platform", "macosx", "--app-icon", "orchevian",
         # Matches LSMinimumSystemVersion in the spec.
         "--minimum-deployment-target", "12.0",
         "--output-partial-info-plist", str(out / "partial.plist"),
         "--output-format", "human-readable-text", "--errors", "--warnings"],
        check=True,
    )
    for name in ("Assets.car", "orchevian.icns"):
        if not (out / name).is_file():
            raise RuntimeError(f"actool did not write {name}; the build needs Xcode 26 or later")


def main() -> int:
    # Keep PyInstaller's cache inside the project, including on managed build hosts.
    env = os.environ | {"PYINSTALLER_CONFIG_DIR": str(ROOT / "build" / "pyinstaller-cache")}
    # Notices for exactly the packages in this environment, which the spec bundles.
    subprocess.run(
        [sys.executable, "scripts/third_party_notices.py", "build/THIRD_PARTY_NOTICES.txt"],
        cwd=ROOT, check=True,
    )
    if sys.platform == "darwin":
        compile_macos_icon()
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "packaging/orchevian.spec"],
        cwd=ROOT, env=env, check=True,
    )
    dist = ROOT / "dist"
    if sys.platform == "darwin":
        bundle = dist / "Orchevian.app"
        executable = bundle / "Contents" / "MacOS" / "Orchevian"
    else:
        bundle = dist / "Orchevian"
        executable = bundle / ("Orchevian.exe" if sys.platform == "win32" else "Orchevian")
    report_path = dist / "smoke-report.json"

    def smoke(program: Path) -> None:
        report_path.unlink(missing_ok=True)
        # Test outside the checkout so source files cannot conceal missing resources.
        with TemporaryDirectory(prefix="orchevian-bundle-check-") as temporary:
            result = subprocess.run(
                [str(program), "--smoke-test", str(report_path)],
                cwd=temporary, timeout=90, check=False,
            )
        report = (json.loads(report_path.read_text(encoding="utf-8"))
                  if report_path.exists() else {})
        if result.returncode or not report.get("ok") or not report.get("frozen"):
            raise RuntimeError(f"Desktop smoke check failed: {report or result.returncode}")
        print(json.dumps(report, indent=2))

    smoke(executable)
    signer = sign_macos.identity() if sys.platform == "darwin" else None
    notary = sign_macos.notary_auth() if signer else None
    if signer:
        # Sign before anything is archived, then check the hardened app still passes.
        sign_macos.sign_app(bundle, signer)
        smoke(executable)
        if notary:
            sign_macos.notarize(bundle, notary)

    def sign_image(image: Path) -> None:
        sign_macos.sign_dmg(image, signer)
        if notary:
            sign_macos.notarize(image, notary)

    version =tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    name = f"Orchevian-{version}-{sys.platform}-{platform.machine().lower()}"
    if sys.platform == "win32":
        archive = Path(shutil.make_archive(str(dist / name), "zip", dist, bundle.name))
    else:
        # Tar preserves the executable modes and symlinks used by Qt and macOS bundles.
        archive = dist / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz", dereference=False) as output:
            output.add(bundle, arcname=bundle.name)
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8",
    )
    print(f"Built and verified {archive}")
    # The download people install: a disk image, an installer, or an AppImage.
    if sys.platform == "darwin":
        download = installers.macos_dmg(bundle, dist, version, smoke,
                                        sign_image if signer else None)
    elif sys.platform == "win32":
        download = installers.windows_installer(bundle, dist, version)
    else:
        download = installers.linux_appimage(bundle, dist, version)
    print(f"Built {download}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
