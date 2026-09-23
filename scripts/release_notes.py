"""Print GitHub Release notes for a tag: its CHANGELOG section plus install steps.

    python3 scripts/release_notes.py v1.0.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INSTALL = """
## Download

| System | File |
| --- | --- |
| macOS (Apple Silicon) | `Orchevian-{v}-macos-arm64.dmg` |
| Windows (64-bit) | `Orchevian-{v}-windows-x64-setup.exe` |
| Linux (64-bit) | `Orchevian-{v}-linux-x64.AppImage` |

**macOS:** open the `.dmg` and drag Orchevian into Applications. This build isn't signed
by Apple yet, so the first time, right-click Orchevian in Applications and choose
**Open**, then **Open** again.

**Windows:** run the installer. If SmartScreen warns about an unrecognized app, choose
**More info → Run anyway**. No administrator rights are needed.

**Linux:** make the AppImage executable (`chmod +x Orchevian-*.AppImage`) and run it.

Orchevian runs AI models on your computer. Install [Ollama](https://ollama.com) to use
Ollama models, then open **Models** in Orchevian to download one.

Each file has a `.sha256` checksum you can use to verify the download.
"""


def section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for heading in (rf"\[{re.escape(version)}\]", r"\[Unreleased\]"):
        match = re.search(rf"^## {heading}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
        if match:
            return match.group(1).strip()
    return ""


def main() -> int:
    version = sys.argv[1].removeprefix("v")
    print(section(version))
    print(INSTALL.format(v=version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
