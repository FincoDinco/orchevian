# Security policy

Orchevian is built to keep your data on your computer. If you find a way to break that,
please report it privately so it can be fixed before it's public.

## Reporting a vulnerability

Use GitHub's private reporting: open the repository's **Security** tab and choose
**Report a vulnerability**. Please include:

- what an attacker could do, and what they would need (for example a malicious file,
  web page, model, or another program on the same computer);
- steps to reproduce, and the Orchevian version and operating system.

Please don't open a public issue or share details publicly until a fix is released.
You'll get a reply within 7 days.

How Orchevian protects your data is described in [docs/security.md](docs/security.md).

## Supported versions

Security fixes go into the latest release. Please update before reporting.

## Scope

In scope: the Orchevian app, its engine, the local API, file readers and creators, web
search, and the desktop builds.

Out of scope: vulnerabilities in model runtimes (Ollama, llama.cpp, MLX), in the models
themselves, or in third-party services, which should be reported to those projects. The
content a model writes can be wrong or misleading; that alone is not a vulnerability.
