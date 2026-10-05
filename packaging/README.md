# Desktop builds

Orchevian ships as a normal desktop app with its own icon: a `.dmg` for macOS, an
installer for Windows, and an AppImage for Linux. Python, Qt and the engine are
included, so people don't need Python or a terminal.

The model runtimes are bundled too: llama.cpp for GGUF models on every platform (Metal
on macOS, CPU on Windows and Linux) and MLX on Apple Silicon Macs. Installing the app
installs these runtimes inside its bundle; no separate runtime download or setup is
required. Model weights are downloaded separately from **Models**.
[Ollama](https://ollama.com) remains a separate, optional install.

## Build locally

Build on the target operating system with Python 3.13, in a separate environment so
development extras can't leak into the app:

```sh
# macOS / Linux
UV_PROJECT_ENVIRONMENT=.venv-package uv sync --locked --no-default-groups --extra gui --extra mlx --extra gguf --group packaging
.venv-package/bin/python scripts/build_desktop.py
```

```powershell
# Windows PowerShell
$env:UV_PROJECT_ENVIRONMENT = ".venv-package"
uv sync --locked --no-default-groups --extra gui --extra mlx --extra gguf --group packaging
.venv-package\Scripts\python.exe scripts\build_desktop.py
```

The script:

1. writes `build/THIRD_PARTY_NOTICES.txt` from the packaging environment;
2. runs PyInstaller with `packaging/orchevian.spec` (icon, version and app metadata);
3. runs the bundled app's smoke check outside the checkout, with temporary data and a
   fake model (database, model worker, local API with its key, document readers, images
   and OCR plumbing, web reading, file creation, Markdown, app icon and secret storage,
   the bundled llama.cpp and MLX runtimes, and the main window);
4. writes a portable archive and the platform download into `dist/`, each with a
   `.sha256` checksum:

| Platform | Download | Notes |
| --- | --- | --- |
| macOS | `Orchevian-<version>-macos-arm64.dmg` | Drag-to-Applications disk image. The app inside the mounted image is smoke-checked again. |
| Windows | `Orchevian-<version>-windows-x64-setup.exe` | Needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`iscc`). Installs per user, no administrator prompt; Start menu and optional desktop shortcut; uninstaller. |
| Linux | `Orchevian-<version>-linux-x64.AppImage` | Needs [appimagetool](https://github.com/AppImage/appimagetool) (`APPIMAGETOOL` or on `PATH`). |

On macOS, Apple's developer tools must be installed and their license accepted
(`sudo xcodebuild -license`).

Missing Inno Setup or appimagetool fails the build. A portable archive alone is not
a successful installer build.

## Verify the downloaded installer

The preview and release workflows both pass their downloads to
`.github/workflows/verify-installers.yml`. Each platform gets a fresh hosted runner
with no app Python packages, Ollama, MLX, or llama.cpp installed by the workflow.
Python runs only the standard-library verification harness:

```sh
python3 scripts/check_installer.py --downloads dist --report dist/install-smoke-report.json
```

The check requires one platform installer with a matching SHA-256 sidecar. It copies
the Mac app out of its DMG before launching it; on Linux it extracts the AppImage and
runs `AppRun` because hosted runners lack FUSE. On Windows it silently installs,
checks, and uninstalls the app; **use only a disposable host** and add
`--disposable-host`. It can affect the machine's installer registration.

The installed app must report that it is frozen and that its platform runtimes load.
It runs outside the source checkout, from a path with spaces, with Python environment
overrides removed and temporary app data. A JSON report records failures as well as
success. Release publication depends on all three installed-app checks passing.

On macOS the report also records Gatekeeper's verdict on the disk image and the
installed app, and whether each carries a stapled notarization ticket.
`--require-notarized`, which the release workflow passes, fails the check unless both
are accepted as notarized.

These checks do not establish real-model inference quality, a first launch from a
browser download (with quarantine), SmartScreen, Linux FUSE support, or
upgrade/rollback behavior. Track those manual checks in
[the release checklist](../docs/release-checklist.md).

## macOS signing and notarization

`scripts/sign_macos.py` signs inside out with the hardened runtime and a secure
timestamp: every library outside a framework, then each Qt framework, then the app.
It then submits the app to Apple's notary service, staples the ticket, builds the
DMG from the stapled app, and signs, notarizes and staples the DMG too. The app needs
no entitlements: the frozen smoke check, including llama.cpp and MLX on the GPU,
passes under the hardened runtime.

`scripts/build_desktop.py` does this only when the environment provides credentials:

- `MACOS_SIGNING_IDENTITY`: the Developer ID Application identity (name or SHA-1).
- `NOTARY_PROFILE`: a local `xcrun notarytool store-credentials` profile, or in CI
  `NOTARY_API_KEY_PATH`, `NOTARY_API_KEY_ID` and `NOTARY_API_ISSUER_ID`.

Without an identity the build stays unsigned; with an identity but no notary
credentials it signs without notarizing. In CI, `scripts/ci_apple_signing.sh` imports
the certificate from repository secrets into a temporary keychain. **Desktop packages**
signs when the secrets are available (not for forks or Dependabot); **Release** fails
without them. Each notarization usually takes a few minutes.

## Icons

`scripts/make_icons.py` draws the icon and writes every format to `packaging/icons/`:
`.ico` (Windows), PNGs (Linux, and the window icon), and for macOS `orchevian.icon`, an
Icon Composer document with the ring, orbit and node as separate layers. Edit the script
and rerun it to change the icon; the generated files are committed.

On macOS, `scripts/build_desktop.py` compiles `orchevian.icon` with Xcode's `actool`
(Xcode 26 or later) into `Assets.car`, which macOS 26 and later draw as Liquid Glass in
every icon style (default, dark, clear and tinted), and a flattened `.icns` for macOS
12–15. To preview the styles without building, render them with Icon Composer's
`ictool` (inside `Xcode.app/Contents/Applications/Icon Composer.app/Contents/Executables`),
for example `--rendition ClearDark`. The app doesn't set a window icon on macOS, because
that would replace the Dock icon with a flat picture. `orchevian.icns` is the
pre-Liquid Glass macOS icon and is no longer bundled.

## Publishing a release

1. Set the version in `pyproject.toml`, `src/llm_manager_app/__init__.py`, and
   `src/llm_engine/__init__.py` (a test checks they match).
2. Rename the changelog's `[Unreleased]` section to the new version.
3. Commit, then tag and push: `git tag v1.0.0 && git push origin v1.0.0`.

`.github/workflows/release.yml` checks that the tag matches the version, builds on
macOS, Windows and Linux (installing Inno Setup and a checksum-pinned appimagetool),
then verifies the installed downloads on fresh runners before publishing a GitHub
Release with the downloads, checksums, and install notes from
`scripts/release_notes.py`. The **Desktop packages** workflow builds the same files for
pull requests without publishing.

## Licenses in the build

Each build includes `LICENSE` (Orchevian's GPL-3.0) and `THIRD_PARTY_NOTICES.txt`,
generated by `scripts/third_party_notices.py`. It lists Python, every bundled package with
its license texts, and Qt/PySide6 under LGPL-3.0 (text in `packaging/licenses/`) with links
to their source code. Qt stays as separate shared libraries so people can replace it, as
the LGPL requires. Both files are viewable in **Help → About Orchevian**.

## Packaging decisions

- The spec lists data PyInstaller can't discover: SQL schema and migrations (loaded by
  path), icons and SVGs, Markdown and Uvicorn modules loaded dynamically, PowerPoint
  templates, ReportLab fonts and PDFium.
- llama.cpp compiles from source during `uv sync` with `GGML_NATIVE=OFF` (set in
  `pyproject.toml`), so the bundle targets a portable CPU baseline (AVX2 on x64) instead of
  the build machine's processor. Windows and Linux builds are CPU-only; GPU backends (CUDA,
  Vulkan) are not bundled. Delete `.venv-package` after changing those settings.
- The spec collects the runtimes' native libraries and the model architectures `mlx_lm`
  imports by name, and fails the build if llama.cpp (or MLX on macOS) is missing. torch
  is excluded; `mlx_lm` only needs `transformers` for tokenizers.
- The smoke check loads both runtimes in a spawned worker. Set `ORCHEVIAN_SMOKE_MODEL_DIR`
  to a folder with `gguf/` and `mlx/` models to also generate text with the smallest of each.
- The entry point calls `multiprocessing.freeze_support()` before importing Qt, as
  [PyInstaller requires](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing)
  for the app's worker processes.
- Directory bundles avoid unpacking the runtime on every launch.

## Not done yet

- **Windows code signing.** The installer is unsigned, so SmartScreen warns on first
  launch; the release notes explain how to continue. Signing needs a Windows
  code-signing certificate.
- **GPU acceleration on Windows and Linux.** Bundled llama.cpp is CPU-only there.
- **Intel Macs.** Builds follow the build machine's architecture; an Intel (x64) macOS
  build needs its own runner.
- **Clean-machine testing** of each download before a public release.
