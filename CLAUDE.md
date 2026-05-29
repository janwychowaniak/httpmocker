# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

httpmocker is a standalone HTTP REST API mocker for integration and end-to-end testing. It serves predefined responses based on JSON configuration, simulating backend APIs with configurable delays and detailed logging.

## Development Commands

### Running the Server

```bash
# Basic usage
python -m httpmocker -p 8080 -c config.json

# Using the example configuration
python -m httpmocker -p 8080 -c config_example.json

# Validate configuration without starting server
python -m httpmocker -p 8080 -c config.json --validate-config
```

### Testing

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_config_loader.py -v
```

### Docker

```bash
# Build image
docker build -t httpmocker:latest .

# Run with mounted config
docker run --name httpmocker-instance -p 8080:8080 \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest
```

### Linting

```bash
# Run pylint on the codebase
pylint httpmocker/
```

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

Each endpoint in `config.json` requires:
- `method` (string): Uppercase HTTP method (GET, POST, PUT, DELETE, etc.)
- `path` (string): Exact path including leading slash
- `status` (integer): HTTP status code (100-599)
- `delay_ms` (integer): Response delay in milliseconds (≥0)
- `payload_inline` (object) OR `payload_file` (string): Response body

### Testing Philosophy

The codebase includes comprehensive unit tests in `tests/test_config_loader.py`:
- Pydantic validation tests for all edge cases
- Configuration loading with valid/invalid JSON
- Payload file validation
- Edge cases: zero delays, complex paths, multiple endpoints

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

- **bottle==0.13.4**: Lightweight WSGI web framework
- **pydantic==2.11.7**: Data validation and settings management
- **rich==14.1.0**: Rich terminal formatting and colors

Development dependencies (requirements-dev.txt):
- **pytest==8.3.5**: Testing framework
- **pylint==3.3.7**: Code linting
- **code2flow==2.5.1**: Code visualization

## File Structure

```
httpmocker/
├── httpmocker/               # Main package
│   ├── __init__.py
│   ├── __main__.py          # Entry point
│   ├── config_loader.py     # Configuration & validation
│   ├── request_handler.py   # HTTP handling & routing
│   └── console_formatter.py # Logging & output
├── tests/                   # Unit tests
│   └── test_config_loader.py
├── payloads/                # External payload files
│   ├── example.json
│   └── urls_list.json
├── config_example.json      # Example configuration
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
└── Dockerfile              # Container image
```
