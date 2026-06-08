# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

httpmocker is a standalone HTTP REST API mocker for integration and end-to-end testing. It serves predefined responses based on JSON configuration, simulating backend APIs with configurable delays and detailed logging.

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
import is exempted via a per-module override in `pyproject.toml`.

### Pre-commit Hooks

```bash
# Install hooks once (runs ruff lint+format and basic hygiene on each commit)
pip install pre-commit
pre-commit install

# Run all hooks against the whole tree
pre-commit run --all-files
```

### Continuous Integration

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main` and on
pull requests: a lint/format/types job (`ruff check` + `ruff format --check` +
`mypy`), a dependency-audit job (`pip-audit`), a Docker image scan (Trivy), and a
test job across Python 3.10–3.14.

## Architecture

### Core Components

The application follows a clean separation of concerns with four main modules:

1. **`__main__.py`** - CLI entry point
   - Argument parsing (`-p/--port`, `-c/--config`, `--validate-config`)
   - Port availability checking
   - Server lifecycle management (startup/shutdown)
   - Top-level error handling and graceful Ctrl+C handling

2. **`config_loader.py`** - Configuration management
   - Pydantic models for validation (`Endpoint`, `Config`)
   - Strict validation: HTTP methods must be uppercase, status codes 100-599, non-negative delays
   - Each endpoint requires exactly one of `payload_inline` or `payload_file`
   - Validates payload files exist at startup
   - All errors result in `SystemExit(1)` with clean error messages

3. **`request_handler.py`** - HTTP request processing
   - Creates Bottle application with endpoint routing
   - Uses dictionary lookup for fast endpoint matching: `(method, path) -> Endpoint`
   - Implements interruptible delays using chunked sleep (100ms chunks)
   - Handles HTTP semantics correctly:
     - `204 No Content`: no Content-Type header, empty body
     - `HEAD` requests: same headers as GET, but empty body with Content-Length set
   - Unmatched requests return 404 with `{"error": "endpoint not found"}`
   - Query parameters are logged but ignored for matching
   - Missing payload files crash intentionally (fail-fast principle)

4. **`console_formatter.py`** - Rich console output
   - Uses the `rich` library for colored, formatted logging
   - Logs all incoming requests with headers, query strings, and request bodies
   - Pretty-prints JSON payloads with syntax highlighting
   - Shows matched endpoint configuration and response timing
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
- **Fail-fast on errors**: Missing payload files crash the application rather than returning 500s
- **URL decoding**: Bottle automatically decodes URL-encoded paths (configure paths in decoded form)
- **Interruptible delays**: Delays can be terminated with Ctrl+C (chunked sleep implementation)
- **JSON support**: Top-level JSON arrays `[]` are supported via `payload_file`; `payload_inline` accepts JSON objects `{}` only

### Configuration Structure

Each endpoint in the config file requires:
- `method` (string): Uppercase HTTP method (GET, POST, PUT, DELETE, etc.)
- `path` (string): Exact path including leading slash
- `status` (integer): HTTP status code (100-599)
- `delay_ms` (integer): Response delay in milliseconds (≥0)
- `payload_inline` (object) OR `payload_file` (string): Response body

### Testing Philosophy

The codebase includes comprehensive unit tests organized by module:
- `tests/test_config_loader.py` - Pydantic validation, config loading (valid/invalid JSON), payload file validation, and edge cases (zero delays, complex paths, multiple endpoints)
- `tests/test_request_handler.py` - the WSGI app: endpoint matching, method/status handling, HTTP semantics (204, HEAD), payload files (object/array), and delays
- `tests/test_console_formatter.py` - payload-source formatting, request/response logging, and lifecycle banners
- `tests/test_main.py` - CLI argument parsing, port-availability checks, and the `--validate-config` path

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
curl -I http://localhost:8080/api/users  # HEAD request
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
│   └── py.typed                # PEP 561 typing marker
├── tests/                      # Unit tests
│   ├── test_config_loader.py
│   ├── test_request_handler.py
│   ├── test_console_formatter.py
│   └── test_main.py
├── configs/                    # API configuration files
│   └── example.json            # Example config (copy to your own)
├── payloads/                   # External payload files
│   ├── example.json
│   └── urls_list.json
├── .github/                    # CI & automation
│   ├── workflows/
│   │   └── ci.yml              # Lint/format/types, audit, Docker scan, tests
│   └── dependabot.yml          # Grouped dependency updates
├── docker-compose.example.yml  # Example Docker Compose setup
├── Dockerfile                  # Container image
├── .dockerignore               # Docker build-context exclusions
├── .pre-commit-config.yaml     # Pre-commit hooks (ruff lint+format, mypy, hygiene)
├── pyproject.toml              # Packaging, dependencies & tooling config
├── uv.lock                     # Fully-pinned dependency lockfile
├── .gitignore
├── CHANGELOG.md                # Notable changes
├── README.md                   # User-facing documentation
├── CLAUDE.md                   # Guidance for Claude Code
└── LICENSE                     # MIT license
```
