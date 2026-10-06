"""Write THIRD_PARTY_NOTICES.txt for a desktop build from the packaging environment.

Run with the same interpreter that builds the app, so the notices list exactly the
bundled packages:

    .venv-package/bin/python scripts/third_party_notices.py build/THIRD_PARTY_NOTICES.txt
"""

from __future__ import annotations

import sys
from importlib.metadata import distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Build tools that are not part of the frozen app. PyInstaller's bootloader is,
# so PyInstaller stays. dmgbuild and its helpers only lay out the macOS disk image.
BUILD_ONLY = {"altgraph", "macholib", "pyinstaller-hooks-contrib", "setuptools", "pip", "wheel",
              "dmgbuild", "ds-store", "mac-alias"}
# Orchevian itself, including installs under its former package names.
OWN = {"orchevian", "llm-engine", "llm-manager"}
QT_PACKAGES = {"pyside6", "pyside6-addons", "pyside6-essentials", "shiboken6"}
LICENSE_WORDS = ("LICENSE", "LICENCE", "COPYING", "NOTICE")


def _license(metadata) -> str:
    expression = metadata.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split(" :: ")[-1] for c in metadata.get_all("Classifier") or []
                   if c.startswith("License ::")]
    return "; ".join(classifiers) or (metadata.get("License") or "See license text").splitlines()[0]


def _homepage(metadata) -> str:
    for url in metadata.get_all("Project-URL") or []:
        label, _, link = url.partition(",")
        if label.strip().lower() in {"homepage", "home", "source", "repository"}:
            return link.strip()
    return metadata.get("Home-page") or ""


def _texts(dist) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or []:
        name = str(file)
        # Vendored packages inside a distribution are covered by its own notices.
        # Match license files and anything in a licenses folder (pypdfium2 names its
        # files after their SPDX identifiers), but never code modules.
        if ("_vendor" in name or name.endswith((".py", ".pyc", ".pyi"))
                or not any(word in name.upper() for word in LICENSE_WORDS)):
            continue
        try:
            data = Path(dist.locate_file(file)).read_bytes()
            texts.append((name, data.decode("utf-8", errors="replace")))
        except (OSError, FileNotFoundError):
            continue
    return texts


def build() -> str:
    parts = [
        "THIRD-PARTY SOFTWARE NOTICES\n"
        "============================\n\n"
        "Orchevian is licensed under the GNU General Public License v3.0 or later (see\n"
        "LICENSE). This build also includes the third-party software listed below, each\n"
        "under its own license. Their notices and license texts follow.\n"
    ]
    python_license = (Path(sys.base_prefix) / "lib"
                      / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt")
    parts.append(section(
        f"Python {sys.version.split()[0]}", "Python Software Foundation License",
        "https://www.python.org",
        [("LICENSE.txt", python_license.read_text(encoding="utf-8"))]
        if python_license.is_file() else [],
    ))
    qt_seen = False
    for dist in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        key = name.lower().replace("_", "-")
        if key in BUILD_ONLY or key in OWN:
            continue
        if key in QT_PACKAGES:
            if not qt_seen:
                parts.append(qt_section(dist.version))
                qt_seen = True
            continue
        parts.append(section(f"{name} {dist.version}", _license(dist.metadata),
                             _homepage(dist.metadata), _texts(dist)))
    return "\n".join(parts)


def qt_section(version: str) -> str:
    lgpl = (ROOT / "packaging" / "licenses" / "LGPL-3.0.txt").read_text(encoding="utf-8")
    note = (
        f"Qt {version} and Qt for Python (PySide6, Shiboken6) {version} are used under the GNU\n"
        "Lesser General Public License v3.0 (LGPL-3.0). The LGPL-3.0 text follows; it builds\n"
        "on the GNU GPL v3.0 included in LICENSE.\n\n"
        "The Qt libraries are included as separate shared library files, so you may replace\n"
        "them with your own compatible build. Source code:\n"
        f"  Qt:           https://download.qt.io/official_releases/qt/{version.rsplit('.', 1)[0]}/"
        f"{version}/\n"
        "  Qt for Python: https://code.qt.io/cgit/pyside/pyside-setup.git/\n"
        "Qt itself includes third-party components; see https://doc.qt.io/qt-6/licenses-used-in-qt.html\n"
        "Help > About Qt in Orchevian shows Qt's own notice."
    )
    return section(f"Qt / PySide6 {version}", "LGPL-3.0-only", "https://www.qt.io",
                   [("Notice", note), ("LGPL-3.0", lgpl)])


def section(title: str, license_name: str, homepage: str, texts) -> str:
    body = [f"\n{'=' * 78}\n{title}\nLicense: {license_name}"]
    if homepage:
        body.append(f"Homepage: {homepage}")
    for name, text in texts:
        body.append(f"\n--- {name} ---\n{text.strip()}")
    if not texts:
        body.append("(No license file is distributed with this package; see its homepage.)")
    return "\n".join(body) + "\n"


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "THIRD_PARTY_NOTICES.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build(), encoding="utf-8")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
