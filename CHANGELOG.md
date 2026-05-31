# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- PEP 621 packaging via `pyproject.toml` (hatchling build backend), a
  `httpmocker` console entry point, and a single source of truth for the
  version (`httpmocker.__version__`).
- Ruff as the linter and formatter, configured in `pyproject.toml`.
- Static type checking with mypy (`strict`, pydantic plugin), wired into
  pre-commit and CI, plus a `py.typed` marker so the package ships its types.
- Dependabot configuration for the GitHub Actions, pip, and Docker
  ecosystems, with grouped updates and a release cooldown.

### Changed
- Pinned GitHub Actions in CI to full commit SHAs (with version comments)
  instead of mutable tags, to harden the workflow against tag-movement
  supply-chain attacks. Dependabot keeps the SHAs and comments updated.
- GitHub Actions CI (lint/format plus tests on Python 3.10–3.13) and
  pre-commit hooks.
- Test suites for the request handler, console formatter, and CLI, plus
  coverage reporting (`pytest-cov`, `fail_under = 85`).
- `HEALTHCHECK` in the Docker image to verify the server port is accepting
  connections.
- This changelog.

### Changed
- Modernized type hints to PEP 585/604 syntax.
- Raised the minimum supported Python version to 3.10.
- Consolidated dependencies into `pyproject.toml` as the single source of
  truth; the Docker image now installs the package directly with `pip install .`.

### Removed
- `requirements.txt` and `requirements-dev.txt`, superseded by `pyproject.toml`
  and its `[dev]` extra.
- The `code2flow` development dependency.
