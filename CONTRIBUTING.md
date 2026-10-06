# Contributing to Orchevian

Thanks for helping improve Orchevian. Bug reports, ideas, and pull requests are welcome.

## Reporting bugs and ideas

Open an [issue](https://github.com/FincoDinco/orchevian/issues) describing what happened,
what you expected, and your operating system. Please don't include private chats or files.
For security problems, follow [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Making changes

1. Fork the repository and create a branch.
2. Set up the development environment and run the checks described in
   [docs/development.md](docs/development.md):

   ```bash
   uv sync --locked --extra gui --extra mlx --extra gguf --group dev
   uv run --no-sync ruff check .
   uv run --no-sync pytest -q
   ```

3. Keep changes focused, add tests for new behavior, and update the documentation when
   behavior changes.
4. Open a pull request explaining what changed and why.

## License and sign-off

Orchevian is licensed under the [GNU GPL v3.0 or later](LICENSE). By contributing, you
agree that your contribution is licensed under the same terms.

Every commit must be signed off to certify the
[Developer Certificate of Origin](https://developercertificate.org/): that you wrote the
change, or otherwise have the right to submit it under this license. Add the sign-off with:

```bash
git commit -s -m "Describe your change"
```

This adds a line like `Signed-off-by: Your Name <you@example.com>` to the commit. Use your
real name and an email address you control.
