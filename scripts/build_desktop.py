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


def main() -> int:
    # Keep PyInstaller's cache inside the project, including on managed build hosts.
    env = os.environ | {"PYINSTALLER_CONFIG_DIR": str(ROOT / "build" / "pyinstaller-cache")}
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
    report_path.unlink(missing_ok=True)
    # Test outside the checkout so source files cannot conceal missing bundle resources.
    with TemporaryDirectory(prefix="orchevian-bundle-check-") as temporary:
        result = subprocess.run(
            [str(executable), "--smoke-test", str(report_path)],
            cwd=temporary, timeout=90, check=False,
        )
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    if result.returncode or not report.get("ok") or not report.get("frozen"):
        raise RuntimeError(f"Desktop smoke check failed: {report or result.returncode}")
    print(json.dumps(report, indent=2))

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
