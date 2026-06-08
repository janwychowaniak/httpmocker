# httpmocker

[![CI](https://github.com/janwychowaniak/httpmocker/actions/workflows/ci.yml/badge.svg)](https://github.com/janwychowaniak/httpmocker/actions/workflows/ci.yml)

Simple HTTP REST API mocker for integration and end-to-end testing.
A standalone server that serves predefined responses based on request method and path configuration.

## Features

- **Standalone HTTP server** - runs as CLI application on configurable port
- **Static configuration** - JSON-based endpoint-to-response mapping
- **Flexible payloads** - inline JSON objects, or external payload files (JSON objects or arrays)
- **Configurable delays** - simulate network latency per endpoint
- **Detailed logging** - verbose console output showing all requests and responses
- **Graceful shutdown** - handles Ctrl+C cleanly, interrupting any in-flight response delay
- **Exact matching** - precise method+path matching for predictable behavior

## Installation

```bash
git clone https://github.com/janwychowaniak/httpmocker.git
cd httpmocker
pip install .
```

This installs the `httpmocker` command along with its dependencies. After
installation you can invoke it as `httpmocker` (or `python -m httpmocker`).

> **Using [uv](https://docs.astral.sh/uv/)?** Run `uv tool install .` from the
> cloned directory to install the `httpmocker` CLI into an isolated environment
> (like `pipx`). For a locked development setup, see [Development](#development).

## Quick Start

```bash
cp configs/example.json configs/config.json          # 1. Copy the example configuration
python -m httpmocker -p 8080 -c configs/config.json  # 2. Run the mock server

curl http://localhost:8080/api/users                 # 3. Test with curl
```

## Usage

```bash
python -m httpmocker -p <port> -c <config_file>
```

**Options:**
- `-p, --port`: HTTP server port (required)
- `-c, --config`: Path to JSON configuration file (required)
- `--validate-config`: Validate configuration file and exit (don't start server)

### Configuration Validation

You can validate your configuration file without starting the server:

```bash
python -m httpmocker -p 8080 -c configs/config.json --validate-config
```

This will:
- Parse and validate the JSON structure
- Check all endpoint configurations
- Verify that all payload files exist
- Display a summary and exit

Example output:
```
✓ Configuration file 'configs/config.json' is valid
✓ Found 8 endpoint(s)
✓ All payload files exist
```

## Configuration Format

```json
{
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/users",
      "status": 200,
      "payload_file": "payloads/example.json",
      "delay_ms": 100
    },
    {
      "method": "POST",
      "path": "/api/login",
      "status": 401,
      "payload_inline": {
        "error": "invalid_credentials",
        "message": "Username or password is incorrect",
        "code": 401
      },
      "delay_ms": 50
    },
    {
      "method": "GET",
      "path": "/health",
      "status": 200,
      "payload_inline": {"status": "healthy"},
      "delay_ms": 0
    }
  ]
}
```

### Configuration Fields

Each endpoint requires:

- **method** (string): HTTP method in uppercase (GET, POST, PUT, DELETE, etc.)
- **path** (string): Exact path to match (case-sensitive, trailing slash matters)
- **status** (integer): HTTP status code to return
- **delay_ms** (integer): Response delay in milliseconds
- **payload_inline** (object) OR **payload_file** (string): Response body content

**Note:**
Use _either_ `payload_inline` for simple JSON objects _or_ `payload_file` for complex responses stored in separate files.

## Example Payloads

Create payload files in the `payloads/` directory. A payload file can hold a
top-level JSON **object** `{}` or a top-level JSON **array** `[]` — note that
`payload_inline` accepts objects only, so arrays must use `payload_file`. The
repo ships one example of each:

**JSON object file — `payloads/example.json`** (excerpt; the full file lists three users):

```json
{
  "users": [
    {
      "id": 1,
      "username": "alice_admin",
      "email": "alice@example.com",
      "role": "administrator",
      "status": "active",
      "created_at": "2024-01-01T12:00:00Z",
      "profile": {
        "first_name": "Alice",
        "last_name": "Johnson",
        "department": "Engineering"
      }
    }
  ],
  "pagination": {
    "total": 3,
    "page": 1,
    "per_page": 10,
    "total_pages": 1
  },
  "meta": {
    "retrieved_at": "2024-01-15T10:30:00Z",
    "api_version": "v1"
  }
}
```

**JSON array file — `payloads/urls_list.json`:**

```json
[
  {"url": "https://api.example.com/v1/data"},
  {"url": "https://service.test.org/endpoint"},
  {"url": "https://mock.demo.net/api/users"},
  {"url": "https://sample.localhost:3000/health"},
  {"url": "https://placeholder.dev/api/status"}
]
```

## Console Output

httpmocker provides detailed logging of all requests and responses:

```
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Connection received on localhost 127.0.0.1:51616
GET /api/users?filter=active HTTP/1.1
Content-Length     54
Content-Type       application/json
Host               127.0.0.1:8080
User-Agent         curl/8.5.0
Accept             */*
X-Client           test-suite

{
  "filter_criteria": {
    "status": "active",
    "limit": 10
  }
}

----------------------------------------------------------------------

Response: GET /api/users
 - status:         200
 - payload_file:   payloads/example.json
 - delay_ms:       150
...sent

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Connection received on localhost 127.0.0.1:51632
POST /api/nonexistent HTTP/1.1
Content-Type       text/plain
Host               127.0.0.1:8080
User-Agent         curl/8.5.0
Accept             */*

----------------------------------------------------------------------

Response: 404 (no match for POST /api/nonexistent)
...sent
```

## Behavior Details

### Request Matching

- **Method**: Case-sensitive, must be uppercase (GET, POST, PUT, DELETE)
- **Path**: Exact string match, trailing slashes matter (`/api/users` ≠ `/api/users/`)
- **Query parameters**: Displayed in logs but ignored for matching
- **Headers**: Logged but not used for matching

### Response Behavior

- **Content-Type**: Automatically set to `application/json` for responses with content
- **JSON Support**: Both JSON objects `{}` and JSON arrays `[]` are supported as top-level responses
- **HTTP Semantics**:
  - `204 No Content` responses omit Content-Type header and have empty body
  - `HEAD` requests return same headers as corresponding GET but with no response body
- **Unmatched requests**: Return HTTP 404 with `{"error": "endpoint not found"}`
- **Missing payload files**: Application crashes with error description (attention to config correctness forced intentionally)
- **Delays**: Implemented as interruptible sleep, can be terminated with Ctrl+C

### URL Encoding

Bottle automatically decodes URL-encoded paths, so configure paths in decoded form:
- URL: `/api/users%20test` → Config path: `/api/users test`

## Project Structure

```
httpmocker/
├── httpmocker/                  # Main package
│   ├── __init__.py             # Package metadata & version
│   ├── __main__.py             # CLI entry point
│   ├── config_loader.py        # Configuration parsing & validation
│   ├── request_handler.py      # HTTP request processing & routing
│   ├── console_formatter.py    # Console logging & output
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

## Example Use Cases

### Testing Error Scenarios

```json
{
  "endpoints": [
    {
      "method": "POST",
      "path": "/api/payment",
      "status": 503,
      "payload_inline": {
        "error": "service_unavailable",
        "retry_after": 30
      },
      "delay_ms": 5000
    }
  ]
}
```

### Testing Collection APIs

```json
{
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/suspicious-urls",
      "status": 200,
      "payload_file": "payloads/urls_list.json",
      "delay_ms": 100
    }
  ]
}
```

Top-level JSON arrays must be served from a `payload_file` — `payload_inline` accepts JSON objects only.

### Testing HEAD Requests

```json
{
  "endpoints": [
    {
      "method": "HEAD",
      "path": "/api/resource-check",
      "status": 200,
      "payload_inline": {},
      "delay_ms": 0
    },
    {
      "method": "DELETE",
      "path": "/api/sessions",
      "status": 204,
      "payload_inline": {},
      "delay_ms": 50
    }
  ]
}
```

### Simulating Slow APIs

```json
{
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/reports/heavy",
      "status": 200,
      "payload_inline": {
        "report": "generated",
        "rows": 100000
      },
      "delay_ms": 3000
    }
  ]
}
```

### Multiple Environment Configs

Keep one config per scenario in the `configs/` directory and pick one at run
time with `-c`:

- `configs/dev.json` - Development API responses
- `configs/staging.json` - Staging environment simulation
- `configs/error.json` - Error scenario testing
- `configs/performance.json` - High-latency simulation

```bash
python -m httpmocker -p 8080 -c configs/staging.json
```

## Docker Usage

### Building the Image

```bash
docker build -t httpmocker:latest .
```

### Running with Docker

**Basic usage:**
```bash
docker run --name httpmocker-instance -p 8080:8080 \
  -v $(pwd)/configs/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest
```

**With custom network (for testing containerized apps):**
```bash
# Create a network
docker network create testing

# Run httpmocker on the network
docker run --name httpmocker --network testing \
  -v $(pwd)/configs/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest

# Run your app on the same network
docker run --network testing \
  -e API_BASE_URL=http://httpmocker:8080 \
  myapp:latest
```

**Using Docker Compose:**
```bash
# Copy the example compose file
cp docker-compose.example.yml docker-compose.yml

# Edit docker-compose.yml to add your services
# Then run:
docker-compose up
```

### Docker Examples

**Testing a web application:**
```bash
# Start httpmocker
docker run -d --name api-mock \
  -p 8080:8080 \
  -v $(pwd)/configs/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest

# Test your app against the mock
curl http://localhost:8080/api/users
```

**Sidecar pattern with custom network:**
```bash
# Create network
docker network create myapp-test

# Start mock service
docker run -d --name mock-api --network myapp-test \
  -v $(pwd)/configs/test-config.json:/app/config.json:ro \
  -v $(pwd)/test-payloads:/app/payloads:ro \
  httpmocker:latest

# Start your application
docker run --network myapp-test \
  -e API_URL=http://mock-api:8080 \
  myapp:test
```

## Development

### Requirements

- Python 3.10+
- Runtime dependencies (bottle, pydantic, rich) are declared in `pyproject.toml`
  and installed automatically by `pip install .`

### Development Environment

Plain `pip` works everywhere and is what CI uses; [uv](https://docs.astral.sh/uv/)
is supported on top for a reproducible, fully-pinned environment from `uv.lock`:

```bash
# pip: resolve dev dependencies fresh from pyproject.toml's ranges
pip install -e ".[dev]"

# uv: install the exact versions pinned in uv.lock (prefix commands with `uv run`)
uv sync --extra dev
uv lock              # regenerate the lockfile after changing dependencies
```

### Running Tests

```bash
# Run unit tests
python -m pytest tests/ -v

# Validate configuration
python -m httpmocker -p 8080 -c configs/example.json --validate-config
```

### Linting & Formatting

```bash
ruff check .          # lint
ruff check . --fix    # lint + autofix
ruff format .         # format
```

### Type Checking

```bash
mypy httpmocker/      # static type check (strict mode)
```

### Pre-commit Hooks

Install the git hooks once so linting, formatting, and type checking run automatically on every commit:

```bash
pip install pre-commit
pre-commit install

# Optional: run all hooks against the whole tree
pre-commit run --all-files
```

### Manual Testing

**Local development:**
```bash
# Start the mock server
python -m httpmocker -p 8080 -c configs/example.json

# In another terminal, test with curl
curl -X GET http://localhost:8080/api/users
curl -X POST http://localhost:8080/api/login -d '{"username":"test"}'
curl -X GET http://localhost:8080/health
```

**Docker development:**
```bash
# Validate locally before containerizing
python -m httpmocker -p 8080 -c configs/config.json --validate-config

# Build and test the Docker image
docker build -t httpmocker:latest .
docker run --name httpmocker-dev -p 8080:8080 \
  -v $(pwd)/configs/example.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

MIT License - see LICENSE file for details.

## Inspiration

This tool is inspired by `netcat -lv` and similar network debugging tools, providing a simple way to observe and mock HTTP API interactions during development and testing.
