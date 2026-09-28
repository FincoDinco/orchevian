# Release checklist

Status: September 28, 2026. Version remains 0.1.0 while preparing the first public
release. A passing unit suite or runtime import check does not establish that every
supported machine can install the app and generate text.

## Current evidence

| Check | Status | Evidence or limitation |
| --- | --- | --- |
| Existing full local test suite | Passed | 707 tests passed before the September 28 fixes; 36 targeted memory/packaging tests passed afterward. |
| Installer verification tests | Passed | 12 installer and notices tests; checksum rejection, required runtimes, host isolation, Windows cleanup, and missing build tools. |
| Lint | Passed | `ruff check .` |
| Apple Silicon app and DMG | Passed locally | Rebuilt app and mounted DMG passed all 12 frozen smoke checks. |
| Installed Mac copy | Passed locally | `dist/install-smoke-report.json`: checksum verified, app copied out of DMG into a temporary path with spaces, image ejected, all 12 checks passed. This is a development Mac, not a clean machine. |
| Windows/Linux installers and fresh-runner verification | Pending | New workflow configured; not yet executed. |
| Real-model file generation | Prior bounded pass | `dist/artifact-acceptance-14/report.json` and all nine cases in `dist/model-matrix-08/report.json`. Not native Office acceptance. |
| Signing and notarization | Pending | Only an Apple Development identity was available locally; no repository signing secrets were configured. |
| Native Word/Excel/PowerPoint review | Pending | Office UI access was not approved; no native-app acceptance is claimed. |

## Build and installation

- [ ] Run **Tests** and **Desktop packages** on the exact candidate commit on all
  three platforms. The repository was made public on September 28 after scanning
  the remote history and current tree for credentials; only the `YOUR_API_KEY`
  documentation placeholder was flagged. Standard hosted runs no longer depend on
  the exhausted private-repository minutes allowance.
- [ ] Keep each `installed-desktop-*` artifact as evidence. The verification job
  starts on a separate runner, installs no app Python packages or inference runtimes,
  and tests the downloaded installer rather than the build directory.
- [ ] Test ordinary launch on clean supported machines: macOS Apple Silicon,
  Windows x64, and Linux x64. Record OS version, CPU, RAM, app version, and checksum.
  Linux's CI extraction check does not verify the normal FUSE launch path.
- [ ] Without Python or Ollama installed, download a small GGUF model from **Models**,
  generate a reply, stop generation, unload, restart, and resume the saved chat.
  Repeat with an MLX model on Apple Silicon. Record model IDs and results.
- [ ] Check a copy of an existing library through installation, upgrade, and rollback;
  verify chats, projects, notes, files, and settings remain available. Preserve a
  backup before testing. Never perform rollback experiments against the only copy.
- [ ] Check uninstall/reinstall behavior and retained user data on Windows.

## Native Office acceptance

Use copies of the existing generated fixtures, so recalculation or Office saves do
not overwrite the original evidence:

- [ ] Word: open `dist/artifact-acceptance-14/primary/proposal.docx` and the revision;
  inspect every page for overflow, page breaks, tables, and headings.
- [ ] Excel: open `dist/artifact-acceptance-14/primary/budget.xlsx`; force
  recalculation, verify formulas/totals against the source brief, inspect formats
  and charts, save a copy, and reopen it without repair warnings.
- [ ] PowerPoint: open `dist/artifact-acceptance-14/primary/briefing.pptx`; inspect
  every slide and speaker notes for overflow and missing content, then present it.
- [ ] Repeat spreadsheet/slide checks for revisions in `dist/model-matrix-08`.
  Record Office versions and specific findings; parser or app-preview success alone
  does not complete these checks.

## Signing and publication

- [ ] Obtain/configure a **Developer ID Application** identity for macOS and
  notarization credentials. An Apple Development certificate does not substitute
  for Developer ID distribution. See [Apple's Developer ID documentation](https://developer.apple.com/help/account/certificates/create-developer-id-certificates).
- [ ] Implement and validate macOS signing, notarization, and stapling; verify the
  downloaded app through normal Gatekeeper launch on a clean Mac.
- [ ] Configure and validate Windows code signing; inspect the downloaded installer's
  signature and normal installation behavior.
- [ ] Finish acceptance before updating all three version fields and the changelog.
- [ ] Push a release tag only when ready to publish. The release workflow waits for
  all three installed-download checks, but those checks do not replace the manual
  gates above. Recheck final signed artifact checksums and retain the reports.
