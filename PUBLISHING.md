# Publishing

This repository is publish-ready for GitHub as
`holo-q/deepseek-responses-proxy`.

## Release surface

- Package: `deepseek-responses-proxy`
- Current version: `0.1.0`
- CLI entry point: `deepseek-responses-proxy`
- Python: `>=3.11`
- Build backend: `uv_build`
- Verification: `uv run python -m unittest discover -s tests -v`,
  `uvx ruff check`, `uv build`

## GitHub setup

```bash
gh repo create holo-q/deepseek-responses-proxy --public --source . --remote origin --push
```

Use `--private` instead of `--public` if this should remain organization
internal while the Codex/DeepSeek behavior is still moving.

## License status

No open-source license is declared yet. That keeps the default copyright posture
conservative until Holo-Q chooses a license intentionally.

## Pre-release checklist

1. Confirm the repository visibility.
2. Choose and add a license if this should be open source.
3. Run the verification commands.
4. Create the GitHub repository and push `master`.
5. Enable branch protection after CI is green.
