"""Tell people when a newer Orchevian release is on GitHub, and where to download it.

The check is one HTTPS request to GitHub's public releases API: no account, cookies, or
details about the person beyond what any web request carries. GitHub's "latest" release
excludes drafts and pre-releases.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass

import httpx

from llm_manager_app import __version__

RELEASES_API = "https://api.github.com/repos/FincoDinco/orchevian/releases/latest"
RELEASES_PAGE = "https://github.com/FincoDinco/orchevian/releases/latest"
# Setting this to 0 turns the automatic check off, as the tests and smoke check do.
ENV_CHECK = "ORCHEVIAN_UPDATE_CHECK"


@dataclass(frozen=True)
class Update:
    version: str
    notes_url: str  # The release page, with what's new.
    download_url: str  # This computer's installer, or the release page if none fits.
    file_name: str  # Empty when download_url is the release page.


def automatic_checks_allowed() -> bool:
    return os.environ.get(ENV_CHECK, "1") != "0"


def parse_version(text: str) -> tuple[int, ...] | None:
    """(1, 2, 3) for "v1.2.3" or "1.2.3"; None for anything else, such as "1.2.3rc1"."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def installer_suffix(system: str = sys.platform, machine: str = "") -> str | None:
    """The end of this computer's installer name in a release, as the build names it."""
    machine = (machine or platform.machine()).lower()
    if system == "darwin":
        return "-macos-arm64.dmg" if machine == "arm64" else None  # No Intel build.
    if system == "win32":
        return "-windows-x64-setup.exe" if machine in {"amd64", "x86_64"} else None
    if system.startswith("linux"):
        return "-linux-x64.AppImage" if machine in {"x86_64", "amd64"} else None
    return None


def latest_release() -> dict:
    response = httpx.get(
        RELEASES_API, timeout=10, follow_redirects=True,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"Orchevian/{__version__}"},
    )
    response.raise_for_status()
    return response.json()


def newer_release(current: str = __version__, *, fetch=latest_release,
                  suffix: str | None = None) -> Update | None:
    """The latest release if it is newer than `current`, else None. Network errors raise."""
    release = fetch()
    version = parse_version(str(release.get("tag_name", "")))
    installed = parse_version(current)
    if version is None or installed is None or version <= installed:
        return None
    notes = str(release.get("html_url") or RELEASES_PAGE)
    suffix = installer_suffix() if suffix is None else suffix
    for asset in release.get("assets") or []:
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if suffix and name.endswith(suffix) and url.startswith("https://"):
            return Update(".".join(map(str, version)), notes, url, name)
    return Update(".".join(map(str, version)), notes, notes, "")
