# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- README: a quick start with the published Docker image that needs no clone; how
  the container finds the config and the payload files (working directory, port,
  permissions); validating a config through the image; a Compose service on the
  published image; the config validation rules; and a Limitations section, which
  notes among other things that the server handles one request at a time.

### Changed
- `.gitignore` ignores user payload files in `payloads/` as it already ignored user
  configs in `configs/`, so only the shipped examples stay tracked.
- `.dockerignore` keeps `configs/`, `payloads/` and the mypy and ruff caches out of
  the build context (33 MB down to under 400 kB), in place of an entry for an example
  config that no longer exists. The image itself is unchanged.

### Fixed
- `docker stop` now shuts the server down cleanly, as Ctrl+C does. The image runs
  the server as PID 1, where a SIGTERM without a handler is ignored, so Docker
  waited out its 10-second timeout and killed the process (exit code 137); outside
  a container SIGTERM ended it at once, without the shutdown message. SIGTERM now
  stops the server with exit code 0, which also covers systemd and Kubernetes.

## [0.1.0] - 2026-10-14

`ghcr.io/janwychowaniak/httpmocker@sha256:91b18cf842127676584065401dd03e8ea010341d7d6f035e2a025440e6b64fa7`

Image Id `sha256:1adb1e644982ae216b88e9d48956461acd607880f74b5ec634b77ecb74255516`, after a
pull and after `docker load` of the release file alike. Python 3.14.8. The digest was read
from the workflow's push and from a pull of the tag; the image can be pulled without logging
in. The release workflow ran for the first time with this tag, and every step of both jobs
passed. (The tagged commit dates this release 2026-10-13, the day it was prepared; it was
tagged and published on 2026-10-14.)

### Added
- PEP 621 packaging via `pyproject.toml` (hatchling build backend), a
  `httpmocker` console entry point, and a single source of truth for the
  version (`httpmocker.__version__`).
- Ruff as the linter and formatter, configured in `pyproject.toml`.
- Static type checking with mypy (`strict`, pydantic plugin), wired into
  pre-commit and CI, plus a `py.typed` marker so the package ships its types.
- GitHub Actions CI (lint/format plus tests on Python 3.10–3.14) and
  pre-commit hooks.
- Test suites for the request handler, console formatter, and CLI, plus
  coverage reporting (`pytest-cov`, `fail_under = 85`).
- `HEALTHCHECK` in the Docker image to verify the server port is accepting
  connections.
- Dependabot configuration for the GitHub Actions, uv, and Docker
  ecosystems, with grouped updates and a release cooldown. The native uv
  ecosystem keeps `pyproject.toml` and `uv.lock` in sync in the same PR.
- `pip-audit` dependency vulnerability scan in CI (auditing the resolved
  runtime dependency tree), plus `pip-audit` in the `[dev]` extra.
- Trivy image scan in CI that builds the Docker image and fails on fixable
  HIGH/CRITICAL vulnerabilities.
- `uv.lock` for reproducible, fully-pinned dependency resolution with
  hashes (`pyproject.toml` remains the source of truth for version ranges).
- Secret scanning with gitleaks: `.gitleaks.toml` (built-in rules plus
  prefix-anchored OpenRouter and Anthropic key rules), pre-commit hooks that
  scan staged changes, commit messages and pushed commits, and a CI workflow
  that scans the full history and all commit messages on every push, pull
  request and weekly.
- Release workflow: on an annotated `vX.Y.Z` tag it checks the tag against
  the package version, runs the suite, builds and smoke-tests the wheel and
  the image, scans the image with Trivy, and publishes it to GHCR
  (`ghcr.io/janwychowaniak/httpmocker`, version and `latest`, amd64). The
  GitHub release gets the image as a `docker save` archive (verified to load
  back as the same image), the wheel, the sdist and `SHA256SUMS`, with the tag
  message as its notes.
- This changelog.

### Changed
- Moved the example configuration into a dedicated `configs/` directory
  (`config_example.json` → `configs/example.json`), mirroring the `payloads/`
  layout, so user-managed configurations have a home separate from project code.
  `.gitignore` keeps the shipped `configs/example.json` tracked while ignoring
  other `configs/*.json`. Note: `payload_file` paths stay relative to the working
  directory (run from the project root), not to the config file's location.
- Modernized type hints to PEP 585/604 syntax.
- Raised the minimum supported Python version to 3.10.
- Consolidated dependencies into `pyproject.toml` as the single source of
  truth; the Docker image now installs the package directly with `pip install .`.
  The image build and runtime were verified end-to-end against a live registry
  (`docker build`, container reaching `healthy` via the `HEALTHCHECK`, and the
  example endpoints served correctly).
- Pinned GitHub Actions in CI to full commit SHAs (with version comments)
  instead of mutable tags, to harden the workflow against tag-movement
  supply-chain attacks. Dependabot keeps the SHAs and comments updated.
- Pinned the Docker base image (`python:3.14-slim`) by digest for
  reproducible, tamper-evident builds.
- Upgraded the Docker base image to Python 3.14 and added 3.14 to the CI test
  matrix, so the shipped runtime stays within the tested range (3.10–3.14).
  Advertised 3.14 support via a trove classifier in `pyproject.toml`.

### Removed
- `requirements.txt` and `requirements-dev.txt`, superseded by `pyproject.toml`
  and its `[dev]` extra.
- The `code2flow` development dependency.

### Fixed
- Request logs now show the client's source port instead of `host:unknown`.
  The default `wsgiref` server does not populate `REMOTE_PORT`, so a custom
  WSGI request handler now exposes the client port to the application.

### Security
- Refreshed the Docker base image to `python:3.14.8-slim` (pinned by digest),
  picking up Debian fixes for HIGH vulnerabilities in util-linux and OpenSSL.
- Removed pip from the runtime Docker image after installing the package. The
  server never needs it, and the libraries pip vendors (urllib3, msgpack,
  setuptools) carried HIGH vulnerabilities that failed the Trivy image scan.
