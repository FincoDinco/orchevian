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

**macOS:** open the `.dmg`, drag Orchevian into Applications, and open it from there.
The app is signed and notarized by Apple.

**Windows:** run the installer. If SmartScreen warns about an unrecognized app, choose
**More info → Run anyway**. No administrator rights are needed.

**Linux:** make the AppImage executable (`chmod +x Orchevian-*.AppImage`) and run it.

Orchevian runs AI models on your computer. GGUF models work on every system, and MLX
models on Apple Silicon Macs, with nothing else to install. Open **Models** in Orchevian
to download one. To use Ollama models too, install [Ollama](https://ollama.com).

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
