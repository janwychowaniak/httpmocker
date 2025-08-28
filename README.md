# httpmocker

Simple HTTP REST API mocker for integration and end-to-end testing.
A standalone server that serves predefined responses based on request method and path configuration.

## Features

- **Standalone HTTP server** - runs as CLI application on configurable port
- **Static configuration** - JSON-based endpoint-to-response mapping
- **Flexible payloads** - supports both inline JSON and external payload files
- **Configurable delays** - simulate network latency per endpoint
- **Detailed logging** - verbose console output showing all requests and responses
- **Graceful shutdown** - handles Ctrl+C with immediate termination
- **Exact matching** - precise method+path matching for predictable behavior

## Installation

```bash
git clone https://github.com/janwychowaniak/httpmocker.git
cd httpmocker
pip install -r requirements.txt
```

## Quick Start

```bash
cp config.example.json config.json           # 1. Copy the example configuration
python -m httpmocker -p 8080 -c config.json  # 2. Run the mock server

curl http://localhost:8080/api/users         # 3. Test with curl
```

## Usage

```bash
python -m httpmocker -p <port> -c <config_file>
```

**Options:**
- `-p, --port`: HTTP server port (required)
- `-c, --config`: Path to JSON configuration file (required)

## Configuration Format

```json
{
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/users",
      "status": 200,
      "payload_inline": {
        "users": [
          {"id": 1, "name": "John Doe"},
          {"id": 2, "name": "Jane Smith"}
        ]
      },
      "delay_ms": 100
    },
    {
      "method": "POST",
      "path": "/api/login",
      "status": 401,
      "payload_file": "payloads/login_error.json",
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

Create payload files in the `payloads/` directory:

**payloads/login_error.json:**

```json
{
  "error": "invalid_credentials",
  "message": "Username or password is incorrect",
  "code": 401
}
```

**payloads/user_list.json:**

```json
{
  "users": [
    {
      "id": 1,
      "username": "admin",
      "email": "admin@example.com",
      "role": "administrator"
    },
    {
      "id": 2,
      "username": "user1",
      "email": "user1@example.com", 
      "role": "user"
    }
  ],
  "total": 2,
  "page": 1
}
```

## Console Output

httpmocker provides detailed logging of all requests and responses:

```
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Connection received on localhost 59392
GET /api/users?filter=active HTTP/1.1
Host:        localhost:8080
User-Agent:  curl/7.81.0
Accept:      */*
X-Client:    test-suite

{
    "filter_criteria": {
        "status": "active",
        "limit": 10
    }
}

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

Response: GET /api/users
 - status:         200
 - payload_inline: {"users": [...]}
 - delay_ms:       100

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Connection received on localhost 59393
POST /api/nonexistent HTTP/1.1
Host:        localhost:8080
User-Agent:  curl/7.81.0
Accept:      */*

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

Response: 404 (no match for POST /api/nonexistent)
```

## Behavior Details

### Request Matching

- **Method**: Case-sensitive, must be uppercase (GET, POST, PUT, DELETE)
- **Path**: Exact string match, trailing slashes matter (`/api/users` ≠ `/api/users/`)
- **Query parameters**: Displayed in logs but ignored for matching
- **Headers**: Logged but not used for matching

### Response Behavior

- **Content-Type**: Automatically set to `application/json` for all responses
- **Unmatched requests**: Return HTTP 404 with `{"error": "endpoint not found"}`
- **Missing payload files**: Application crashes with error description (attention to config correctness forced intentionally)
- **Delays**: Implemented as interruptible sleep, can be terminated with Ctrl+C

### URL Encoding

Bottle automatically decodes URL-encoded paths, so configure paths in decoded form:
- URL: `/api/users%20test` → Config path: `/api/users test`

## Project Structure

```
httpmocker/
├── config.example.json       # Example configuration file
├── httpmocker/
│   ├── __init__.py
│   ├── __main__.py           # CLI entry point  
│   ├── config_loader.py      # Configuration parsing and validation
│   ├── request_handler.py    # HTTP request processing
│   └── console_formatter.py  # Output formatting
├── payloads/                 # Directory for payload files
│   └── example.json
├── README.md
├── requirements.txt
└── LICENSE
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

### Simulating Slow APIs

```json
{
  "endpoints": [
    {
      "method": "GET", 
      "path": "/api/reports/heavy",
      "status": 200,
      "payload_file": "payloads/large_report.json",
      "delay_ms": 3000
    }
  ]
}
```

### Multiple Environment Configs

Create different configuration files for various testing scenarios:

- `config.dev.json` - Development API responses
- `config.staging.json` - Staging environment simulation  
- `config.error.json` - Error scenario testing
- `config.performance.json` - High-latency simulation

## Development

### Requirements

- Python 3.8+
- bottle
- pydantic

### Running Tests

```bash
# Test with curl
python -m httpmocker -p 8080 -c config.example.json

# In another terminal
curl -X GET http://localhost:8080/api/users
curl -X POST http://localhost:8080/api/login -d '{"username":"test"}'
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
