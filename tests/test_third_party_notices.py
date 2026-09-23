from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "third_party_notices", ROOT / "scripts" / "third_party_notices.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notices_cover_python_qt_and_installed_packages_with_license_texts():
    text = _module().build()
    assert "\nPython 3." in text and "Python Software Foundation" in text
    assert "Qt / PySide6" in text and "GNU LESSER GENERAL PUBLIC LICENSE" in text
    assert "download.qt.io/official_releases/qt/" in text
    for package in ("pypdf", "python-docx", "openpyxl", "pillow", "fastapi"):
        assert f"\n{package} " in text.lower() or f"\n{package.replace('-', '_')} " in text.lower()
    # Orchevian's own license is LICENSE, not a third-party notice.
    for own in ("orchevian", "llm-engine", "llm-manager"):
        assert f"\n{own} " not in text.split("follow.\n", 1)[1].lower()
    # Every runtime package that ships in the desktop build carries its license text.
    for package in ("pypdfium2", "reportlab", "lxml", "certifi"):
        block = text.lower().split(f"\n{package} ", 1)[1].split("\n" + "=" * 78, 1)[0]
        assert "no license file" not in block and "--- " in block


def test_desktop_build_generates_and_bundles_the_notices():
    build = (ROOT / "scripts" / "build_desktop.py").read_text()
    spec = (ROOT / "packaging" / "orchevian.spec").read_text()
    assert "third_party_notices.py" in build
    assert '"THIRD_PARTY_NOTICES.txt"' in spec and '"LICENSE"' in spec
    assert (ROOT / "packaging" / "licenses" / "LGPL-3.0.txt").is_file()
