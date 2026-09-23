# Desktop builds

This first distribution slice creates a standalone **Orchevian** desktop bundle
with Python, Qt, and the engine included. Users do not need Python or uv to run it.
Ollama must be installed and running separately, with at least one model installed.
Model weights, Ollama, MLX, and llama.cpp are not included. Existing GGUF/MLX files
remain visible, but this build explains that their runtimes are unavailable.

These are development preview packages. There is no installer, update mechanism,
release signing, or macOS notarization yet. The workflow stores build artifacts;
it does not publish a release. Build success is not a claim of real-model or
clean-machine compatibility testing.

## Build locally

Build on the target operating system and architecture, using Python 3.13.
On macOS, Apple's developer tools must be installed and their current license
accepted. If `lipo` reports an unaccepted Xcode license, review it in Terminal with
`sudo xcodebuild -license` before building again.
Use a separate environment so optional inference packages cannot enter the bundle
or get removed from the regular development environment:

```sh
# macOS / Linux
UV_PROJECT_ENVIRONMENT=.venv-package uv sync --locked --no-default-groups --extra gui --group packaging
.venv-package/bin/python scripts/build_desktop.py
```

```powershell
# Windows PowerShell
$env:UV_PROJECT_ENVIRONMENT = ".venv-package"
uv sync --locked --no-default-groups --extra gui --group packaging
.venv-package\Scripts\python.exe scripts\build_desktop.py
```

The script runs PyInstaller, executes the bundled smoke check, then creates an
archive with a SHA-256 sidecar in `dist/`. macOS produces `Orchevian.app` in a
`.tar.gz`; Linux produces an `Orchevian/` folder in a `.tar.gz`; Windows produces an
`Orchevian/` folder in a `.zip`. Extract the entire archive and keep its contents
together. On macOS, the app can be moved to Applications; on Windows/Linux, launch
the executable inside the extracted folder. Unsigned previews may be blocked by
OS security controls; signing and clean-machine launch validation remain release
requirements.

The **Desktop packages** GitHub Actions workflow runs on relevant pull requests
or manually, building separately on Ubuntu 22.04, Windows, and macOS. Each artifact
name identifies its runner; the archive filename identifies its actual architecture.
Linux compatibility starts with the build host's system libraries, and macOS
architecture follows the runner. Additional architectures need their own builds.

## Verification

`scripts/build_desktop.py` runs the actual executable outside the checkout with
`--smoke-test REPORT_JSON`, enforces a 90-second timeout, and requires a successful
report identifying a frozen executable before creating an archive. The check uses
temporary config, database, vault, logs, model directory, and Qt settings. It uses
a fake model and an offscreen Qt platform, with no model downloads or user database
access. It checks:

- Database migration files and SQL schema, plus a template write.
- Isolated Word, Excel, CSV, and text readers, original storage, and unreadable-PDF detection.
- Picture decoding, PDF rendering, and image-bearing generation through a spawned fake vision backend.
- Shared project file retrieval and preservation of reply sources after project deletion.
- Spawned web retrieval, HTML extraction, search-off behavior and retained sources using an offline fixture.
- Isolated DOCX/XLSX/PPTX/PDF-form/YAML creation, rendered previews, version storage and ZIP export.
- A spawned model worker, including load, response generation, and unload.
- A real loopback API listener with JSON and streaming completions.
- Markdown extension imports and SVG asset rendering.
- Main workspace rendering and thread shutdown.

The JSON report remains at `dist/smoke-report.json`. Before distributing a release,
also test the native window and Ollama chat on a clean machine, as well as upgrade
and rollback with a copied existing library. Quit any other Orchevian/legacy app
before opening the normal desktop app; it retains the existing data path and
startup migrations. Do not delete the existing database.

September 22, 2026 verification: the macOS arm64 preview was rebuilt with the
September 21 artifact-revision fixes and passed all ten frozen smoke checks.
A separate check verified the archive checksum, extracted the archive into a
temporary path containing spaces, and reran all ten checks with `PYTHONPATH`,
`PYTHONHOME`, and `VIRTUAL_ENV` removed. Executable permissions survived extraction.
That report is at `dist/archive-smoke-report.json`. Both runs used temporary data
and fake inference on the development Mac; clean-machine launches, native Office
layout/recalculation, and packaged real-model inference remain unverified.
A later September 22 rebuild with the model-matrix and web-connection fixes passed
the same built and extracted-archive checks.

## Licenses in the build

Each build includes `LICENSE` (Orchevian's GPL-3.0) and `THIRD_PARTY_NOTICES.txt`, which
`scripts/third_party_notices.py` generates from the packaging environment before PyInstaller
runs. It lists Python, every bundled package with its license texts, and Qt/PySide6 under
LGPL-3.0 (text in `packaging/licenses/`) with links to their source code. Qt stays as
separate shared libraries so users can replace it, as the LGPL requires. Both files are
viewable in **Help → About Orchevian**.

## Packaging decisions

The [PyInstaller spec](https://pyinstaller.org/en/stable/spec-files.html) explicitly
includes SQL, dynamically loaded migration source files, SVGs, and the modules
loaded dynamically by Uvicorn and Markdown, PowerPoint templates, and ReportLab fonts.
Optional native inference libraries
are excluded even if present on a developer's machine. Use the clean environment
above to keep other accidental dependencies out of the package.

The entry point calls `multiprocessing.freeze_support()` before importing Qt,
following [PyInstaller's multiprocessing guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#multi-processing).
This is required for the disposable model workers in frozen builds. Directory
bundles avoid extracting the runtime on each launch, and POSIX tar archives
preserve bundle symlinks and executable permissions.

Chat document uploads, text reading, and shared project files are implemented in the working tree.
Picture/scanned-document reading now includes Pillow/PDFium previews and Ollama vision.
Local English OCR uses a separately installed Tesseract shared library and language data;
these are not bundled yet. The smoke check verifies rendering and image transport with
a fake model, not real-model interpretation. See the [user guide](../docs/user-guide.md#pictures-and-scanned-pdfs) for OCR setup.
Document creator tools include additional packaged runtime dependencies and a
generator smoke check. Two complete creation/revision fixtures passed with the
installed MLX model on September 21 in the development environment. A live sourced
answer fixture passed on September 18. Broader model quality, native Office layout,
spreadsheet recalculation, and clean-machine validation remain outstanding. Before
release signing and notarization, complete those checks and broader web-search
acceptance. The prompt toggle and search services are implemented; the
offline smoke fixture does not establish provider availability. Web search uses
the standard library, and migration 007 is included by the migration-file glob.
See [the feature milestones](../DESIGN.md#next-feature-milestones--documents-and-project-workspace).
Keep this preview build working as those features add dependencies and resources.

Distribution work follows those milestones: native runtime bundles by platform,
release signing and notarization, installer formats, icons/version metadata, and
clean-machine testing.
