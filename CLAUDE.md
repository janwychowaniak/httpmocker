# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

httpmocker is a standalone HTTP REST API mocker for integration and end-to-end testing. It serves predefined responses based on JSON configuration, simulating backend APIs with configurable delays and detailed logging.

Version 0.3.0 is adding record and replay modes and a request journal. Their contract is
`docs/record-replay.md`: read it before changing anything those features touch, keep it in
sync as the build settles details, and where it differs from the README, it wins.

## Development Commands

### Running the Server

```bash
# Basic usage
python -m httpmocker -p 8080 -c configs/config.json

# Using the example configuration
python -m httpmocker -p 8080 -c configs/example.json

# Validate configuration without starting server
python -m httpmocker -p 8080 -c configs/config.json --validate-config
```

### Testing

```bash
# Install the package with development dependencies
pip install -e ".[dev]"

# Run all tests (coverage is collected automatically; see pyproject.toml)
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_config_loader.py -v
```

Coverage is configured in `pyproject.toml` (`--cov=httpmocker`, term-missing
report, `fail_under = 85`). The suite is dependency-free: the WSGI app is driven
directly via a small in-test client, so no extra web-test framework is needed.

### Dependency Management

The project is a standard PEP 621 / hatchling package, so plain `pip` works
everywhere and is what CI uses. `uv` is layered on top for a reproducible,
fully-pinned developer environment driven by `uv.lock`.

```bash
# Create/refresh a locked dev environment (.venv) from uv.lock
uv sync --extra dev

# Run commands inside that environment
uv run pytest -v
uv run httpmocker -p 8080 -c configs/example.json

# Regenerate the lockfile after editing dependencies in pyproject.toml
uv lock
```

`uv sync` installs the exact versions pinned in `uv.lock`, whereas
`pip install -e ".[dev]"` resolves fresh from the ranges in `pyproject.toml`.
Both work: CI and the published install use pip, while `uv` provides local
reproducibility and is how Dependabot keeps `pyproject.toml` and `uv.lock` in sync.

### Docker

```bash
# Build image
docker build -t httpmocker:latest .

# Run with mounted config
docker run --name httpmocker-instance -p 8080:8080 \
  -v $(pwd)/configs/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest
```

### Linting & Formatting

```bash
# Lint the codebase
ruff check .

# Auto-fix lint issues
ruff check . --fix

# Format the codebase
ruff format .
```

### Type Checking

```bash
# Static type check the package (strict mode, configured in pyproject.toml)
mypy httpmocker/
```

The package is fully type-annotated and ships a `py.typed` marker. mypy runs in
`strict` mode with the pydantic plugin; `bottle` has no type information, so its
import is exempted via a per-module override in `pyproject.toml`, and a second
override lets `request_handler` register its routes with Bottle's untyped decorator.

### Pre-commit Hooks

```bash
# Install hooks once: ruff lint+format, mypy and basic hygiene on each commit,
# plus gitleaks secret scanning on commit, commit message and push
pip install pre-commit
pre-commit install

# Run all hooks against the whole tree
pre-commit run --all-files
```

A plain `pre-commit install` sets up the `pre-commit`, `commit-msg` and `pre-push`
git hooks (`default_install_hook_types`). The gitleaks hooks are local scripts in
`scripts/gitleaks/` that call the system `gitleaks` binary (`apt install gitleaks`)
with the rules in `.gitleaks.toml`; without the binary they warn and skip. They
scan staged content, the commit message (a channel content scans never see), and
every local commit not yet on a remote. Local hooks are advisory: `--no-verify`
skips them, so the CI workflow is the enforcing layer. Validate any
`.gitleaks.toml` change against a corpus of fake keys before committing it, since
a new rule's `keywords` can suppress built-in detections.

### Continuous Integration

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main` and on
pull requests: a lint/format/types job (`ruff check` + `ruff format --check` +
`mypy`), a dependency-audit job (`pip-audit`), a Docker image scan (Trivy), and a
test job across Python 3.10–3.14. Its `concurrency` cancels a run in progress when
a newer push reaches the same branch, on `main` too, by choice: after several quick
pushes the earlier runs show as cancelled, and the run of the last pushed commit is
the one to wait for, since it covers the whole tree.

A separate workflow (`.github/workflows/gitleaks.yml`) scans the full history and
all commit messages for secrets on every push, pull request and weekly.

### Releasing

`.github/workflows/release.yml` runs on an **annotated** `v*` tag. Before anything is
published, the `publish` job checks that the tag is annotated and equals
`v` + `httpmocker.__version__`, runs the suite, builds the wheel and sdist (installing
the wheel into a clean venv), builds the image, scans it with Trivy (the CI gate) and
smoke-tests it against `configs/example.json`. It then pushes
`ghcr.io/janwychowaniak/httpmocker:<version>` and `latest` (amd64; `latest` is not a
contract). The `release` job pulls that image by digest, saves it as
`httpmocker-<version>-image-amd64.tar.gz`, proves the archive loads back as the same image
Id (`scripts/release/archive-image.sh`), and attaches it with the wheel, the sdist and
`SHA256SUMS` to the GitHub release, whose notes must equal the tag message. It is the only
job with `contents: write` and runs after the push: **if it fails, re-run that job alone**,
since re-running the whole workflow builds and pushes again.

Every release, in this order:
1. Set `__version__` in `httpmocker/__init__.py`, move `[Unreleased]` in `CHANGELOG.md` to
   `[X.Y.Z] - YYYY-MM-DD`, and update the version wherever the README names it (the
   release and Docker examples).
2. Push, and wait for CI to be green on that commit.
3. Check every claim in the notes against the code, since a pushed tag message cannot be
   corrected. Then tag the commit with the notes as its message, check that the message
   is the notes, and push the tag:
   ```bash
   git tag -a vX.Y.Z -F notes.txt
   git cat-file tag vX.Y.Z | sed '1,/^$/d' | diff - notes.txt
   git push origin vX.Y.Z
   ```
   Git's default clean-up strips every line that starts with `#`, so the notes carry no
   Markdown headings. `git tag -l --format='%(contents)'` prints an extra blank line, so
   compare with `git cat-file` instead.
4. After the workflow, check what it published:
   - the digest in the workflow's summary is the one a pull of the tag gets without
     logging in (`DOCKER_CONFIG` pointing at a directory whose `config.json` is `{}`);
   - the release files pass `sha256sum -c SHA256SUMS` after
     `gh release download vX.Y.Z --dir <dir> --clobber`;
   - `docker load` of the image archive, after `docker rmi` of the pulled tag, gives the
     same image Id;
   - the release body equals the notes;
   - the release's changes work on the published image.

   Then record under the version's heading in `CHANGELOG.md`, in a follow-up commit, the
   digest, the image Id, the image's Python version and base image, and what was checked.

A tag is never pushed again, not even to fix its message: that could publish the same
version under another digest. A wrong line is corrected in `CHANGELOG.md` instead.
Steps that run only on a tag are exercised by no CI run beforehand, so a change to the
release workflow is checked locally as far as it goes, then by reading its log at the
next release.

## Architecture

### Core Components

The application follows a clean separation of concerns with four main modules:

1. **`__main__.py`** - CLI entry point
   - Argument parsing (`-p/--port`, one mode of `-c/--config`, `--record`, `--replay`,
     `--validate-config`); the record options are checked and normalized here. Record and
     replay are parsed but not served yet: 0.3.0 is building them step by step
   - `ClientPortRequestHandler` adds the request target and headers as received to the
     environ (`httpmocker.raw_target`, `httpmocker.raw_headers`), since wsgiref
     percent-decodes `PATH_INFO` and folds repeated headers
   - Port availability checking
   - Server lifecycle management (startup/shutdown)
   - Top-level error handling and graceful Ctrl+C and SIGTERM handling, also in the
     middle of a request: `ClientPortRequestHandler` runs it through a `ServerHandler`
     that lets the interrupt through, where plain wsgiref answers 500 and keeps serving

2. **`config_loader.py`** - Configuration management
   - Pydantic models for validation (`Endpoint`, `Config`)
   - Strict validation: HTTP methods must be uppercase and one of `SUPPORTED_METHODS` (the
     seven the server routes), paths must start with `/` and contain no `?` or `#`,
     status codes 100-599, non-negative delays
   - Each endpoint requires exactly one of `payload_inline` or `payload_file`
   - Validates at startup that payload files exist and hold valid JSON, reporting every
     problem with them at once; this runs only after the models validate, so a config
     with both kinds of errors shows the payload file problems on the next run
   - All errors result in `SystemExit(1)` with an error message (pydantic's own report for
     model validation errors)

3. **`request_handler.py`** - HTTP request processing
   - Creates Bottle application with endpoint routing for `SUPPORTED_METHODS`; Bottle
     answers any other method with its own 405 HTML page
   - Uses dictionary lookup for fast endpoint matching: `(method, path) -> Endpoint`
   - Implements interruptible delays using chunked sleep (100ms chunks)
   - Handles HTTP semantics correctly:
     - `204 No Content`: no Content-Type header, empty body
     - `HEAD` requests: match only endpoints configured with method `HEAD` (no fallback to
       the `GET` endpoint); Content-Type and Content-Length of the payload, empty body
   - Unmatched requests return 404 with `{"error": "endpoint not found"}`
   - Query parameters are logged but ignored for matching
   - Payload files are read on every request, so edits apply without a restart; one that
     disappears or breaks while the server runs gets a 500 from wsgiref (plain text,
     traceback in the log) and the server keeps serving

4. **`console_formatter.py`** - Rich console output
   - Uses the `rich` library for colored, formatted logging
   - Logs all incoming requests with headers, query strings, and request bodies
   - Pretty-prints JSON request bodies with syntax highlighting
   - Shows the matched endpoint's configuration (status, payload source, delay)
   - Startup banner and graceful shutdown messages

### Data Flow

1. CLI parses arguments → `load_config()` validates JSON and payload files
2. `create_app()` builds Bottle application with endpoint dictionary
3. For each incoming request:
   - Log full request details (method, path, headers, body)
   - Look up `(method, path)` in endpoint dictionary
   - If matched: apply delay → set status → load payload → send response
   - If not matched: return 404
   - Log response details and "sent" confirmation

### Key Design Decisions

- **Exact matching only**: Paths must match exactly (trailing slashes matter, `/api/users` ≠ `/api/users/`)
- **No templating or dynamic routing**: Static configuration for predictable behavior
- **Fail-fast on config errors**: a config the server could not serve (unsupported method,
  path without a leading `/` or with `?` or `#`, missing or invalid payload file)
  stops it at startup rather than surfacing later as failed requests
- **URL decoding**: Bottle automatically decodes URL-encoded paths (configure paths in decoded form)
- **Interruptible delays**: Ctrl+C or SIGTERM during a delay stops the server at once; the
  interrupted connection is closed without a response (chunked sleep implementation)
- **JSON support**: `payload_inline` and `payload_file` both take a top-level JSON object `{}` or array `[]`

### Configuration Structure

Each endpoint in the config file requires:
- `method` (string): Uppercase HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD or OPTIONS
- `path` (string): Exact path, starting with `/`, without `?` or `#`
- `status` (integer): HTTP status code (100-599)
- `delay_ms` (integer): Response delay in milliseconds (≥0)
- `payload_inline` (object or array) OR `payload_file` (string): Response body

### Testing Philosophy

The codebase includes comprehensive unit tests organized by module:
- `tests/test_config_loader.py` - Pydantic validation (including supported methods and the path rules), config loading (valid/invalid JSON), payload file validation (missing, unreadable, invalid JSON), and edge cases (zero delays, complex paths, multiple endpoints)
- `tests/test_request_handler.py` - the WSGI app: endpoint matching, method/status handling, HTTP semantics (204, HEAD), payload files (object/array), and delays
- `tests/test_console_formatter.py` - payload-source formatting, request/response logging, and lifecycle banners
- `tests/test_main.py` - CLI argument parsing (run modes, record options, target URL checks), port-availability checks (including a port left in TIME_WAIT), the `--validate-config` path, shutdown on SIGTERM, and the request handler (client port, interrupted requests, raw target and headers)
- `tests/test_identity.py` - request identity helpers (JSON Pointer parsing so far)

When adding features, follow the existing test structure with descriptive docstrings and organized test classes.

## Common Patterns

### Adding a New Endpoint to Config

```json
{
  "method": "POST",
  "path": "/api/endpoint",
  "status": 201,
  "payload_inline": {"result": "success"},
  "delay_ms": 100
}
```

### Testing Manual Requests

```bash
curl -X GET http://localhost:8080/api/users
curl -X POST http://localhost:8080/api/login -d '{"username":"test"}'
curl -I http://localhost:8080/api/users  # HEAD: configs/example.json has a HEAD endpoint for it
```

## Dependencies

All dependencies are declared in `pyproject.toml`, the single source of truth.

Runtime (`[project.dependencies]`):
- **bottle**: Lightweight WSGI web framework
- **pydantic**: Data validation and settings management
- **rich**: Rich terminal formatting and colors

Development (`[project.optional-dependencies].dev`, installed via `pip install -e ".[dev]"`):
- **pytest** / **pytest-cov**: Testing framework and coverage
- **ruff** (pinned): Linting and formatting
- **mypy**: Static type checking (strict, with the pydantic plugin)
- **pip-audit**: Dependency vulnerability scanning

`uv.lock` pins the fully-resolved dependency graph (with hashes) for
reproducible installs via `uv sync`. It is generated from `pyproject.toml`;
regenerate it with `uv lock` whenever the declared dependencies change.

## File Structure

```
httpmocker/
├── httpmocker/                  # Main package
│   ├── __init__.py             # Package metadata & version
│   ├── __main__.py             # CLI entry point
│   ├── config_loader.py        # Configuration & validation
│   ├── request_handler.py      # HTTP handling & routing
│   ├── console_formatter.py    # Logging & output
│   ├── headers.py              # Transport and default redacted header sets (0.3.0)
│   ├── identity.py             # Request identity for replay (0.3.0, in progress)
│   └── py.typed                # PEP 561 typing marker
├── tests/                      # Unit tests
│   ├── test_config_loader.py
│   ├── test_request_handler.py
│   ├── test_console_formatter.py
│   ├── test_identity.py
│   └── test_main.py
├── configs/                    # API configuration files
│   └── example.json            # Example config (copy to your own)
├── payloads/                   # External payload files
│   ├── example.json
│   └── urls_list.json
├── docs/
│   └── record-replay.md        # Contract of record, replay and the journal (0.3.0)
├── scripts/
│   ├── gitleaks/               # Secret-scan hook scripts (wired via pre-commit)
│   └── release/
│       └── archive-image.sh    # Save the published image and prove it loads back
├── .github/                    # CI & automation
│   ├── workflows/
│   │   ├── ci.yml              # Lint/format/types, audit, Docker scan, tests
│   │   ├── gitleaks.yml        # Secret scan of history and commit messages
│   │   └── release.yml         # On a v* tag: GHCR image + GitHub release files
│   └── dependabot.yml          # Grouped dependency updates
├── docker-compose.example.yml  # Example Docker Compose setup
├── Dockerfile                  # Container image
├── .dockerignore               # Docker build-context exclusions
├── .pre-commit-config.yaml     # Pre-commit hooks (ruff, mypy, hygiene, gitleaks)
├── .gitleaks.toml              # gitleaks rules (defaults + add-on provider rules)
├── pyproject.toml              # Packaging, dependencies & tooling config
├── uv.lock                     # Fully-pinned dependency lockfile
├── smoke-tests.md              # curl calls for every endpoint of configs/example.json
├── .gitignore
├── CHANGELOG.md                # Notable changes
├── README.md                   # User-facing documentation
├── CLAUDE.md                   # Guidance for Claude Code
└── LICENSE                     # MIT license
```
