"""Sign, notarize and staple the macOS app and disk image for Developer ID distribution.

Gatekeeper opens a downloaded app without warnings only when it is signed with a
Developer ID, runs with the hardened runtime, and has been notarized by Apple. Signing
goes inside out: each library outside a framework, then each Qt framework, then the app.

Credentials come from the environment, so builds without them stay unsigned:
MACOS_SIGNING_IDENTITY names the keychain identity. NOTARY_PROFILE names a local
notarytool keychain profile; in CI, NOTARY_API_KEY_PATH, NOTARY_API_KEY_ID and
NOTARY_API_ISSUER_ID give an App Store Connect API key instead.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

# 64-bit, 32-bit and universal Mach-O headers as they appear on disk.
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}


def identity() -> str | None:
    return os.environ.get("MACOS_SIGNING_IDENTITY") or None


def notary_auth() -> list[str] | None:
    if profile := os.environ.get("NOTARY_PROFILE"):
        return ["--keychain-profile", profile]
    if key := os.environ.get("NOTARY_API_KEY_PATH"):
        return ["--key", key, "--key-id", os.environ["NOTARY_API_KEY_ID"],
                "--issuer", os.environ["NOTARY_API_ISSUER_ID"]]
    return None


def is_macho(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(4) in MACHO_MAGIC


def codesign(paths: list[Path], name: str, *, runtime: bool = True) -> None:
    command = ["codesign", "--force", "--timestamp", "--sign", name]
    if runtime:
        command += ["--options", "runtime"]
    # Batches, because every signature waits on Apple's timestamp server.
    for start in range(0, len(paths), 50):
        batch = [str(path) for path in paths[start:start + 50]]
        if subprocess.run(command + batch, check=False).returncode:
            raise RuntimeError("codesign failed; its message above names the file")


def sign_app(app: Path, name: str) -> None:
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    main = app / "Contents" / "MacOS" / info["CFBundleExecutable"]
    frameworks, libraries = [], []
    # os.walk does not follow symlinks, so each real file is signed exactly once.
    for folder, directories, files in os.walk(app / "Contents"):
        here = Path(folder)
        if here.suffix == ".framework":
            frameworks.append(here)
        if any(part.endswith(".framework") for part in here.parts):
            continue
        for file in files:
            path = here / file
            if path != main and not path.is_symlink() and is_macho(path):
                libraries.append(path)
    codesign(libraries, name)
    # Deepest first, in case a framework ever nests another.
    codesign(sorted(frameworks, key=lambda path: len(path.parts), reverse=True), name)
    codesign([app], name)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def sign_dmg(image: Path, name: str) -> None:
    codesign([image], name, runtime=False)
    subprocess.run(["codesign", "--verify", "--strict", str(image)], check=True)


def notarize(path: Path, auth: list[str]) -> None:
    """Submit to Apple, wait for the verdict, and staple the ticket for offline launches."""
    with TemporaryDirectory(prefix="orchevian-notary-") as staging:
        upload = path
        if path.suffix == ".app":
            # notarytool accepts a zip, dmg or pkg; ditto keeps the bundle's symlinks.
            upload = Path(staging) / f"{path.stem}.zip"
            subprocess.run(["ditto", "-c", "-k", "--keepParent", str(path), str(upload)],
                           check=True)
        result = subprocess.run(
            ["xcrun", "notarytool", "submit", str(upload), "--wait", "--timeout", "30m",
             "--output-format", "json", *auth],
            capture_output=True, text=True, check=False,
        )
    try:
        outcome = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"notarytool failed: {result.stdout}{result.stderr}") from None
    if outcome.get("status") != "Accepted":
        log = subprocess.run(["xcrun", "notarytool", "log", outcome.get("id", ""), *auth],
                             capture_output=True, text=True, check=False)
        raise RuntimeError(f"Notarization of {path.name} ended {outcome.get('status')}: "
                           f"{log.stdout or log.stderr}")
    print(f"Notarized {path.name}: submission {outcome['id']}")
    subprocess.run(["xcrun", "stapler", "staple", str(path)], check=True)
    subprocess.run(["xcrun", "stapler", "validate", str(path)], check=True)
    assess(path)


def assess(path: Path) -> None:
    """Ask Gatekeeper the question it asks when someone opens the download."""
    if path.suffix == ".dmg":
        command = ["spctl", "--assess", "--type", "open", "--context",
                   "context:primary-signature", "-vv", str(path)]
    else:
        command = ["spctl", "--assess", "--type", "execute", "-vv", str(path)]
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["sign", "notarize", "assess"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    path = args.path.resolve()
    if args.action == "sign":
        name = identity()
        if name is None:
            parser.error("set MACOS_SIGNING_IDENTITY")
        (sign_dmg if path.suffix == ".dmg" else sign_app)(path, name)
    elif args.action == "notarize":
        auth = notary_auth()
        if auth is None:
            parser.error("set NOTARY_PROFILE or the NOTARY_API_KEY_* variables")
        notarize(path, auth)
    else:
        assess(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
