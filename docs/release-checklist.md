# Release checklist

Status: October 2, 2026. Version remains 0.1.0 while preparing the first public
release. A passing unit suite or runtime import check does not establish that every
supported machine can install the app and generate text.

## Current evidence

| Check | Status | Evidence or limitation |
| --- | --- | --- |
| Existing full local test suite | Passed | 733 tests passed on October 1 with the spreadsheet and source-attribution fixes. |
| Installer verification tests | Passed | 12 installer and notices tests; checksum rejection, required runtimes, host isolation, Windows cleanup, and missing build tools. |
| Lint | Passed | `ruff check .` |
| Apple Silicon app and DMG | Passed locally | Rebuilt app and mounted DMG passed all 12 frozen smoke checks. |
| Installed Mac copy | Passed locally | `dist/install-smoke-report.json`: checksum verified, app copied out of DMG into a temporary path with spaces, image ejected, all 12 checks passed. This is a development Mac, not a clean machine. |
| Installers and fresh-runner verification | Passed in CI | **Desktop packages** run 36897083553 on `75ff16d`: the macOS DMG, Windows installer, and Linux AppImage each passed checksum verification and all 12 checks on a separate runner (reports copied to `dist/installed-desktop-75ff16d/`). Linux runs the extracted AppImage, not the FUSE launch path. The first run (36431655477) failed on Linux only because the harness used `hashlib.file_digest`, which Ubuntu 22.04's Python 3.10 lacks. |
| Real-model file generation | Prior bounded pass | `dist/artifact-acceptance-14/report.json` and all nine cases in `dist/model-matrix-08/report.json`. Not native Office acceptance. |
| macOS signing and notarization | Passed in CI | **Desktop packages** run 37024907968 on `85cc479` signed the app with the repository's Developer ID secrets, notarized and stapled the app and the DMG (Apple submissions e9596daa and 4c6b69a5, both Accepted), and the fresh-runner check reported Gatekeeper **Notarized Developer ID** and a stapled ticket for both (`dist/installed-desktop-85cc479/`). Earlier the same day CI's `75ff16d` app, signed locally with no entitlements, passed 12 checks and 14 with real GGUF and MLX models (`dist/macos-signing-01/`). Three earlier runs failed on setup: the signing secret held an Apple Development identity, then a duplicate intermediate import, then an invalid Issuer ID secret. |
| Windows signing | Pending | No code-signing certificate. |
| Native Word/Excel/PowerPoint review | Passed with findings | `dist/office-acceptance-01/report.json`: Word 16.113.3, Excel 16.113.2, PowerPoint 16.113.3 on macOS 27.0. All nine fixtures opened without repair; totals and formulas recalculated correctly; Excel-saved copies reopened cleanly; decks presented. Three spreadsheet findings fixed and rechecked in Excel (`dist/office-acceptance-02/`); see **Office findings** below. |

## Build and installation

- [ ] Run **Tests** and **Desktop packages** on the exact candidate commit on all
  three platforms. The repository was made public on September 28 after scanning
  the remote history and current tree for credentials; only the `YOUR_API_KEY`
  documentation placeholder was flagged. Standard hosted runs no longer depend on
  the exhausted private-repository minutes allowance. Both passed on all three
  platforms for `75ff16d` (Tests 36897083725, Desktop packages 36897083553); repeat on
  the final candidate.
- [ ] Keep each `installed-desktop-*` artifact as evidence. The verification job
  starts on a separate runner, installs no app Python packages or inference runtimes,
  and tests the downloaded installer rather than the build directory.
No clean machines are available, so on October 1 the clean-machine checks below were
accepted for 1.0 on the CI fresh-runner evidence instead. What that covers and what
it leaves untested:

- [x] ~~Test ordinary launch on clean supported machines.~~ Accepted on CI: each
  installer is downloaded to a separate runner with no app Python packages or model
  runtimes, checksum-verified, installed (Windows), copied out of the DMG (macOS), or
  extracted (Linux), and the frozen app runs its 12 checks, including Qt startup.
  **Not covered:** a normal double-click launch, Gatekeeper and SmartScreen prompts,
  and Linux's FUSE launch path.
- [x] ~~Without Python or Ollama installed, download a model, generate, stop, unload,
  restart, and resume.~~ Accepted on CI: the frozen app loads the bundled llama.cpp
  (and MLX on Apple Silicon, with a test computation) and generates through a spawned
  worker with a stand-in model. CI does not set `ORCHEVIAN_SMOKE_MODEL_DIR`, so real
  models were checked locally instead: CI's checksum-verified `75ff16d` DMG, copied out
  of the image on an Apple M5 Pro running macOS 27.0, passed all 14 checks
  (`dist/real-model-smoke/real-model-report.json`); `gguf/gemma-4-E4B-it-Q8_0` and
  `mlx/avan-ag/Qwen3.5-4B-Uncensored-MLX-4bit` each loaded, generated, and unloaded.
  **Not covered:** real-model generation on Windows and Linux, a download from
  **Models**, and stop, restart and resume in the interface.
- [x] ~~Check uninstall/reinstall behavior and retained user data on Windows.~~
  Accepted on CI: the Windows check installs silently per-user and uninstalls with
  the bundled uninstaller. **Not covered:** reinstalling, and whether user data
  remains after uninstalling.
- [x] Check a copy of an existing library through installation, upgrade, and rollback;
  verify chats, projects, notes, files, and settings remain available. Preserve a
  backup before testing. Never perform rollback experiments against the only copy.
  `dist/library-upgrade-check/report.json`: CI's `75ff16d` app and then the previous
  September 28 build each opened copies of a current library (13 chats, 54 messages,
  a project, a chat file, a generated file) and of an old LLM Manager library with
  test rows. Every table was unchanged except the new tables added to the old library;
  everything read back through the engine services. The old library's automatic backup
  matched it exactly, and migrations 2–7 never alter old tables, so going back keeps
  working. The old LLM Manager app itself was not available to run, and this library
  had no Second Brain notes.

### Other findings

- [ ] **Keychain failures silently fall back to the settings file.** `SecretStore.set`
  (`src/llm_manager_app/secret_store.py`) stores a key in the settings file whenever a
  Keychain write raises, not only where no vault exists, as the changelog says. The
  library check triggered it by changing `HOME`; a locked keychain or a denied access
  prompt would too. Decide whether to report the failure instead.
- Old LLM Manager favourite models stay in the library but Orchevian never shows them.

## Native Office acceptance

Use copies of the existing generated fixtures, so recalculation or Office saves do
not overwrite the original evidence:

- [x] Word: open `dist/artifact-acceptance-14/primary/proposal.docx` and the revision;
  inspect every page for overflow, page breaks, tables, and headings. Also checked
  `dist/model-matrix-08/writing/turn-1/brief.docx`. Each is one page within margins;
  the revision changes only the participant count; the brief omits the planted note.
- [x] Excel: open `dist/artifact-acceptance-14/primary/budget.xlsx`; force
  recalculation, verify formulas/totals against the source brief, inspect formats
  and charts, save a copy, and reopen it without repair warnings. Total $1,200.00;
  the chart has two findings below.
- [x] PowerPoint: open `dist/artifact-acceptance-14/primary/briefing.pptx`; inspect
  every slide and speaker notes for overflow and missing content, then present it.
- [x] Repeat spreadsheet/slide checks for revisions in `dist/model-matrix-08`.
  Record Office versions and specific findings; parser or app-preview success alone
  does not complete these checks. Inventory totals $71.75 and $96.75 as expected;
  the rollout revision preserves slides 1 and 3 and all notes.

### Office findings

The first three are fixed in `_xlsx` (`src/llm_engine/artifacts/generators.py`).
`dist/office-acceptance-02/` holds the budget and both inventory workbooks regenerated
from their original specs, with identical cells, and checked again in Excel 16.113.2:
same totals, clean save and reopen.

- [x] **Chart plots the total.** Charts now stop before trailing Total, Subtotal, or
  Grand total rows; the budget chart shows three item bars.
- [x] **Chart axes hidden.** openpyxl 3.1 omits `<c:delete val="0"/>`, which Excel
  reads as hidden axes. Both axes are now written as visible; Excel shows $0.00–$700.00
  and the item names.
- [x] **Wide sheets print across pages.** Columns are sized to their content (10–60)
  instead of a fixed 22, and sheets print fit to one page wide; the inventory sheet
  now prints all four columns on one page.
- [x] **Internal source IDs in decks.** Notes read `Sources: brief.txt [source: D37e476cb:1]`
  because the file prompt asked for actual source identifiers. Files now credit sources
  by file name: the prompt asks for file names only, any leftover excerpt IDs are
  removed before files are written, and only a file name counts as attribution.
  `dist/model-matrix-09/report.json` reran the slides and writing cases with the same
  MLX model: no IDs in any model output, and notes read `Sources: brief.txt`. The
  writing case still needed one attribution repair, as it did in `model-matrix-08`.
- Cosmetic only: Word tables write numbers as left-aligned plain text; the budget
  slide shows amounts without a currency; the chart title touches the top gridline
  and a one-series chart's legend repeats the category labels.

## Signing and publication

- [x] Obtain/configure a **Developer ID Application** identity for macOS and
  notarization credentials. Done October 1: Developer ID certificate, App Store
  Connect API key, local notarytool profile and five repository secrets.
- [ ] Implement and validate macOS signing, notarization, and stapling; verify the
  downloaded app through normal Gatekeeper launch on a clean Mac. Implemented, and
  passed locally and in CI on October 2 (see **Current evidence**). Remaining: a
  first launch of CI's DMG after a browser download (quarantined), on this Mac
  because no clean Mac is available.
- [ ] Configure and validate Windows code signing; inspect the downloaded installer's
  signature and normal installation behavior.
- [ ] Finish acceptance before updating all three version fields and the changelog.
- [ ] Push a release tag only when ready to publish. The release workflow waits for
  all three installed-download checks, but those checks do not replace the manual
  gates above. Recheck final signed artifact checksums and retain the reports.
