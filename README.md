# httpmocker

Simple HTTP REST API mocker for integration and end-to-end testing.
A standalone server that serves predefined responses based on request method and path configuration.

## Features

- **Standalone HTTP server** - runs as CLI application on configurable port
- **Static configuration** - JSON-based endpoint-to-response mapping
- **Flexible payloads** - supports both inline JSON and external payload files (objects and arrays)
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
cp config_example.json config.json           # 1. Copy the example configuration
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
- `--validate-config`: Validate configuration file and exit (don't start server)

### Configuration Validation

You can validate your configuration file without starting the server:

```bash
python -m httpmocker -p 8080 -c config.json --validate-config
```

This will:
- Parse and validate the JSON structure
- Check all endpoint configurations
- Verify that all payload files exist
- Display a summary and exit

Example output:
```
✓ Configuration file 'config.json' is valid
✓ Found 6 endpoint(s)
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

Create payload files in the `payloads/` directory. httpmocker supports both JSON objects and JSON arrays as top-level responses:

**JSON Object Response (payloads/login_error.json):**

```json
{
  "error": "invalid_credentials",
  "message": "Username or password is incorrect",
  "code": 401
}
```

**JSON Array Response (payloads/urls_list.json):**

```json
[
  {"url": "https://api.example.com/v1/data"},
  {"url": "https://service.test.org/endpoint"},
  {"url": "https://mock.demo.net/api/users"}
]
```

**Complex JSON Object (payloads/user_list.json):**

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
...sent

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Connection received on localhost 59393
POST /api/nonexistent HTTP/1.1
Host:        localhost:8080
User-Agent:  curl/7.81.0
Accept:      */*

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

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
├── config_example.json       # Example configuration file
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

### Testing Collection APIs

```json
{
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/suspicious-urls",
      "status": 200,
      "payload_inline": [
        {"url": "https://malicious.example.com"},
        {"url": "https://phishing.test.org"}
      ],
      "delay_ms": 100
    }
  ]
}
```

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

## Docker Usage

### Building the Image

```bash
docker build -t httpmocker:latest .
```

### Running with Docker

**Basic usage:**
```bash
docker run --name httpmocker-instance -p 8080:8080 \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  httpmocker:latest
```

**With custom network (for testing containerized apps):**
```bash
# Create a network
docker network create testing

# Run httpmocker on the network
docker run --name httpmocker --network testing \
  -v $(pwd)/config.json:/app/config.json:ro \
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
  -v $(pwd)/config.json:/app/config.json:ro \
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
  -v $(pwd)/test-config.json:/app/config.json:ro \
  -v $(pwd)/test-payloads:/app/payloads:ro \
  httpmocker:latest

# Start your application
docker run --network myapp-test \
  -e API_URL=http://mock-api:8080 \
  myapp:test
```

## Development

### Requirements

- Python 3.8+
- bottle==0.13.4
- pydantic==2.11.7
- rich==14.1.0

### Running Tests

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run unit tests
python -m pytest tests/ -v

# Validate configuration
python -m httpmocker -p 8080 -c config_example.json --validate-config
```

### Manual Testing

**Local development:**
```bash
# Start the mock server
python -m httpmocker -p 8080 -c config_example.json

# In another terminal, test with curl
curl -X GET http://localhost:8080/api/users
curl -X POST http://localhost:8080/api/login -d '{"username":"test"}'
curl -X GET http://localhost:8080/health
```

**Docker development:**
```bash
# Validate locally before containerizing
python -m httpmocker -p 8080 -c config.json --validate-config

# Build and test the Docker image
docker build -t httpmocker:latest .
docker run --name httpmocker-dev -p 8080:8080 \
  -v $(pwd)/config_example.json:/app/config.json:ro \
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
