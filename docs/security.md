# Security

Orchevian runs AI models on your own computer and treats everything that comes from
outside, such as web pages, documents, model output and downloaded files, as untrusted.
This page explains what that means in practice. To report a problem, see
[SECURITY.md](../SECURITY.md).

## What leaves your computer

Nothing, unless you ask for it:

- **Web search**, only when you turn it on for a message. Up to 500 characters of that
  question go to the search service (Exa's free search, or a service you added a key for),
  which may keep it under its own privacy policy. Files and saved chats are never sent.
- **Model browsing and downloads** from Hugging Face or Ollama.

There is no telemetry, analytics or account.

## Your data on disk

- Chats, projects, notes, attachments and logs are stored in your data folder
  (normally `~/.local/share/orchevian`). On macOS and Linux the folder is readable only by your
  account (permissions `0700`, files `0600`); older installs are repaired on startup.
  Windows keeps it inside your private user profile.
- Search-service keys and the local API key are kept private to your account: in the
  system password vault (Keychain, Credential Manager, Secret Service) when the `keyring`
  package is installed, otherwise in Orchevian's settings, whose file is owner-only.
- Private Chat is never written to disk.

## Local API

The API is off until you enable it, listens only on `127.0.0.1`, and requires an API
key on every request. It also rejects requests from web pages (any `Origin` header),
accepts only `127.0.0.1` and `localhost` as host names to block DNS-rebinding attacks,
limits request bodies to 4 MB and replies to 32,768 tokens, and keeps no message content.

## Untrusted content

- **Documents and images** are read in a separate, disposable process with time and size
  limits. XML external entities are disabled, archive expansion is capped, pictures are
  checked for size before decoding, and PDF pages are rendered at a bounded size.
- **Web pages** are read only from public addresses. Private, local-network and special
  addresses are refused, every redirect is checked, the connection goes to the verified
  address, HTTPS certificates are verified, and pages are limited to 3 MB. Orchevian never
  scrapes search engines' results pages.
- **Web and document text** is given to the model as reference data, marked as untrusted,
  never as instructions.
- **Model output** can't run code. Replies can't insert raw HTML, images or non-web links
  into the chat, and hovering a link shows where it really goes. Created files come from a
  validated specification: no macros or scripts, spreadsheet formulas limited to basic math
  functions with no external references, formula-like CSV text escaped, and file names
  sanitized.
- **Downloaded models** stay inside your model folder, existing files are never overwritten,
  and Hugging Face files are checked against their published SHA-256 checksums.
- **Second Brain** notes can't be written outside the notes folder, and linked files are
  refused.

## Supply chain

Dependencies are locked to exact versions (`uv.lock`) and checked against the OSV
vulnerability database. GitHub workflows run with read-only permissions and pin
third-party actions to exact commits, and Dependabot proposes updates weekly.

## Known limits

- **Models can be wrong or misled.** A web page or document can contain text written to
  manipulate an AI ("prompt injection"). Orchevian limits what a model can do, but it
  can't guarantee that an answer is true. Check important answers against their sources.
- **Files are not encrypted by Orchevian.** Use your operating system's disk encryption
  (FileVault, BitLocker, LUKS) to protect data if your computer is lost or stolen.
- **Programs running as you can read your files**, as with any desktop app.
- **Preview desktop builds are not yet code-signed.**
