# httpmocker

[![CI](https://github.com/janwychowaniak/httpmocker/actions/workflows/ci.yml/badge.svg)](https://github.com/janwychowaniak/httpmocker/actions/workflows/ci.yml)

Simple HTTP REST API mocker for integration and end-to-end testing.
A standalone server that serves predefined responses based on request method and path
configuration, or records a conversation with a real API and replays it.

## Features

- **Standalone HTTP server** - runs as CLI application on configurable port
- **Static configuration** - JSON-based endpoint-to-response mapping
- **Flexible payloads** - JSON objects or arrays, inline in the config or from external payload files
- **Configurable delays** - simulate network latency per endpoint
- **Detailed logging** - verbose console output showing all requests and responses
- **Graceful shutdown** - handles Ctrl+C and SIGTERM (`docker stop`) cleanly, interrupting any in-flight response delay
- **Exact matching** - precise method+path matching for predictable behavior
- **Record and replay** - record a real API into a cassette, then serve the recording:
  repeated identical requests get the recorded responses in recorded order
- **Request journal** - `GET /_httpmocker/journal` shows what the server received, for
  test assertions; `DELETE` resets it between scenarios
- **Redaction** - credentials never reach the console, the journal or a cassette in clear

## Installation

### From a release

Each [GitHub release](https://github.com/janwychowaniak/httpmocker/releases) attaches the
wheel, the sdist and the Docker image archive, with their `SHA256SUMS`. Install the CLI
straight from the release wheel:

```bash
pip install https://github.com/janwychowaniak/httpmocker/releases/download/v0.3.0/httpmocker-0.3.0-py3-none-any.whl
```

The Docker image is also published to GHCR; see [Docker Usage](#docker-usage).

### From source

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

Only have the Docker image? See [Quick start with the published image](#quick-start-with-the-published-image).

## Usage

```bash
python -m httpmocker -p <port> -c <config_file>                              # serve a config
python -m httpmocker -p <port> --record <target_base_url> --cassette <file>  # record a real API
python -m httpmocker -p <port> --replay <cassette_file>                      # replay a recording
```

**Options:**
- `-p, --port`: HTTP server port (required)
- `-c, --config`: Path to JSON configuration file
- `--record`, `--cassette`: Record a target into a cassette; see [Record and replay](#record-and-replay)
- `--replay`: Serve a recorded cassette; see [Record and replay](#record-and-replay)
- `--validate-config`: Validate the configuration file (`-c`) or the cassette (`--replay`) and exit (don't start server)

Exactly one of `-c`, `--record` and `--replay` is required.

### Configuration Validation

You can validate your configuration file without starting the server:

```bash
python -m httpmocker -p 8080 -c configs/config.json --validate-config
```

This will:
- Parse and validate the JSON structure
- Check all endpoint configurations
- Verify that all payload files exist and hold valid JSON
- Display a summary and exit

Example output:
```
✓ Configuration file 'configs/config.json' is valid
✓ Found 8 endpoint(s)
✓ All payload files exist and hold valid JSON
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

- **method** (string): HTTP method in uppercase: GET, POST, PUT, DELETE, PATCH, HEAD or OPTIONS
- **path** (string): Exact path to match, starting with `/`, without `?` or `#` (case-sensitive, trailing slash matters)
- **status** (integer): HTTP status code to return, from 100 to 599
- **delay_ms** (integer): Response delay in milliseconds, 0 or more
- **payload_inline** (object or array) OR **payload_file** (string): Response body content

**Note:**
Use _either_ `payload_inline` for short responses written into the config _or_ `payload_file` for larger ones stored in separate files. Both take a top-level JSON object `{}` or array `[]`.

A config needs at least one endpoint, and each endpoint exactly one of `payload_inline`
and `payload_file`. Every payload file must exist and hold valid JSON (UTF-8). Otherwise
the server refuses to start and says why: first every error in the endpoints' fields,
then, once those pass, every problem with the payload files. Other fields, such as
`headers`, are ignored (see [Limitations](#limitations)).

`payload_file` paths are resolved relative to the directory the server is started from,
not to the config file. Start it from the directory that holds `payloads/` (the project
root here) and write the paths as `payloads/<file>.json`. The Docker image follows the same
rule; see [How the container sees your files](#how-the-container-sees-your-files).

## Example Payloads

Create payload files in the `payloads/` directory. A payload file can hold a
top-level JSON **object** `{}` or a top-level JSON **array** `[]`, just like
`payload_inline`. The repo ships one example of each:

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

The values of `Authorization`, `Proxy-Authorization`, `Cookie` and `Set-Cookie` print as
`<redacted>`. In record and replay the response line reads
`PUT /api/items/sample → 201 (84 ms) recorded #0` or
`PUT /api/items/sample → 409 served #1 (2/2)` (interaction 1 of the cassette, the second of
two recorded for this request). A miss, an exhausted sequence or a failed exchange with
the target prints a line in white on red.

## Behavior Details

### Request Matching

- **Method**: Case-sensitive: GET, POST, PUT, DELETE, PATCH, HEAD or OPTIONS, in uppercase
- **Path**: Exact string match, trailing slashes matter (`/api/users` ≠ `/api/users/`)
- **Query parameters**: Displayed in logs but ignored for matching, so `/api/users` also
  answers `/api/users?filter=active`. A config path therefore cannot contain `?`, nor `#`,
  since clients do not send the fragment.
- **Headers**: Logged but not used for matching
- **Reserved paths**: `/_httpmocker` and everything under `/_httpmocker/` belong to the
  server itself (the [journal](#the-journal)). A config endpoint there is refused, and a
  request there other than the journal gets `404` with `X-Httpmocker: reserved`.

### Response Behavior

- **Content-Type**: Automatically set to `application/json` for responses with content
- **JSON Support**: Both JSON objects `{}` and JSON arrays `[]` are supported as top-level responses, inline or from a file
- **HTTP Semantics**:
  - `204 No Content` responses omit Content-Type header and have empty body
  - `HEAD` requests match only endpoints configured with method `HEAD`, not the `GET`
    endpoint of the same path; they get the Content-Type and Content-Length of their
    payload, with no body
- **Unmatched requests**: Return HTTP 404 with `{"error": "endpoint not found"}`; a request
  with a method other than the seven above gets a `405` instead (see [Limitations](#limitations))
- **Payload files**: Checked at startup: the server refuses to run if one is missing,
  unreadable or not valid JSON. They are read again on every request, so an edit takes
  effect at once, without a restart. A file that disappears or stops being valid JSON
  while the server runs gets that request a `500` with a plain-text body and a traceback
  in the log, and the server keeps serving.
- **Delays**: Interruptible: Ctrl+C or SIGTERM during a delay stops the server at once, and that request's connection is closed without a response

### URL Encoding

Bottle automatically decodes URL-encoded paths, so configure paths in decoded form:
- URL: `/api/users%20test` → Config path: `/api/users test`

### Limitations

httpmocker serves static JSON responses and nothing else, by design:

- **JSON only**: every configured response is JSON sent as `application/json`; there are
  no custom response headers and no other content types. Two responses come from the
  server itself and are not JSON: a `405` HTML page for a request with any other method,
  and a `500` plain-text page for a payload file that breaks while the server runs.
- **Static responses**: no templating, no responses that depend on the request, and no
  state (such as a different response to the second call) in config mode. For responses
  that change from call to call, [record and replay](#record-and-replay) the real API.
- **Exact matching only**: no wildcards, path parameters or regular expressions.
- **Seven methods**: GET, POST, PUT, DELETE, PATCH, HEAD and OPTIONS. Any other method
  gets `405` and is not journaled, in every mode.
- **One request at a time**: the server is single-threaded, so while one response waits
  out its `delay_ms`, every other request waits as well, whatever its endpoint. In record
  mode they also wait for the target.
- **Plain HTTP**: the server itself speaks no TLS and asks for no authentication (record
  mode can reach an HTTPS target). It listens on all interfaces (`0.0.0.0`), so when
  started from the CLI it is reachable from your network, not only from localhost, and so
  is its journal.
- **Console logging only**: requests and responses are logged to standard output, not
  to a file; the journal keeps the last 10 000 exchanges in memory.

## Record and replay

A config answers every request the same way. An API that keeps state does not: a `PUT`
that creates a resource answers `201` the first time and `409` the second. Record mode
captures such a conversation with the real API in a **cassette** file. Replay mode serves
the cassette: repeated identical requests get the recorded responses in recorded order,
error bodies included, verbatim. The recording is the mock.

The full contract, with every rule and the reasons for it, is
[`docs/record-replay.md`](docs/record-replay.md); this section is the user's guide.

### Recording

Point your client at httpmocker instead of the real API:

```bash
httpmocker -p 8080 --record https://api.example.test --cassette cassettes/items.json \
  --ignore-query-param ts --ignore-json-path /request_id --redact-header X-Api-Key
```

- **Forwarding.** Every request goes to the target base URL as received: the raw path and
  query appended to the base (which may have a path prefix), the headers in order, and the
  body.
  - `Host` is set to the target's.
  - `Accept-Encoding: identity` is sent, so bodies arrive readable.
  - Redirects are returned to the client, not followed.

  The target's status, headers and body go back to the client unchanged.
- **Recording.** Each exchange is appended to the cassette, and the file is complete on disk
  before the client gets its response. The cassette is rewritten atomically after every
  interaction, so stopping httpmocker (Ctrl+C, `docker stop`) never leaves a broken file.
- **A cassette is never overwritten.** `--record` refuses to start on a file that already
  holds interactions. A missing or empty file, or a cassette left without interactions,
  is fine.
- **Methods.** Only GET, POST, PUT, DELETE, PATCH, HEAD and OPTIONS are forwarded.

**Options** (record mode only; each flag can be repeated):

| Option | Effect |
|---|---|
| `--ignore-query-param NAME` | the parameter does not take part in matching on replay (timestamps, nonces); its value is still recorded |
| `--ignore-json-path POINTER` | this [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901) is deleted from JSON request bodies before matching, e.g. `/request_id` |
| `--match-header NAME` | this request header's value takes part in matching; headers play no part by default |
| `--redact-header NAME` | the header's value is hidden in the cassette, the console and the journal; it still reaches the target |
| `--redact-query-param NAME` | the same for a query parameter, which then no longer takes part in matching |
| `--upstream-timeout SECONDS` | deadline for each whole exchange with the target (default 30) |

The identity and redaction lists are stored in the cassette, so replay needs no flags.

**The deadline** runs from the connect to the last byte of the response, however the
target spreads its bytes. Your client sees nothing until that last byte, so set the
deadline **below the client's own timeout**, with a margin: 290 for a client that waits
300 seconds. A target that cannot be reached, fails TLS verification, or misses the
deadline gets the client a `502` with `X-Httpmocker: upstream-error` and the cause; nothing
is recorded, and the session is simply recorded again.

### Replaying

```bash
httpmocker -p 8080 --replay cassettes/items.json
```

**Matching.** A request matches a recorded interaction when it has the same:
- method and path;
- query, in any order, without the ignored and redacted parameters;
- body: JSON is compared as JSON, without the ignored paths, and any other body byte for
  byte;
- values of the matched headers.

**Sequences.** Interactions that match the same request form a sequence: the first such
request gets the first recorded response, the second the second, and so on.

**Miss and exhausted.** A request with no recorded interaction (a **miss**), or one more
than were recorded (**exhausted**), gets:
- `500` with `X-Httpmocker: miss` or `exhausted`;
- the request's key as the body;
- a journal entry, and a line on the console that stands out.

No status code is unambiguous against a real API, so assert on the journal:
`miss + exhausted == 0`. `DELETE /_httpmocker/journal` starts every sequence over: use it
between scenarios.

### The cassette

A cassette is JSON, written to be read and edited by hand. `cassettes/example.json` shows
the format, here shortened to one interaction:

```json
{
  "httpmocker_cassette": 1,
  "target": "https://api.example.test",
  "identity": {"ignore_query_params": ["ts"], "ignore_json_paths": ["/request_id"], "match_headers": []},
  "redacted_headers": ["authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"],
  "interactions": [
    {
      "request": {"method": "PUT", "path": "/api/items/sample", "query": {"ts": ["1761048000"]},
                  "headers": {"content-type": "application/json", "x-api-key": "<redacted>"},
                  "body": {"name": "sample", "request_id": "r-1"}},
      "response": {"status": 201, "headers": {"content-type": "application/json"},
                   "body": {"id": 7, "name": "sample"}},
      "meta": {"recorded_at": "2026-10-21T12:00:01.120Z", "duration_ms": 84, "delay_ms": 0}
    }
  ]
}
```

**Editing by hand.** You can reorder, delete or add interactions, and set `meta.delay_ms` to
replay a response slowly. A body is stored as `body` (JSON), `body_text` or `body_base64`.
JSON bodies are replayed in compact form with sorted keys, so compare them as JSON.

**Validation.** `httpmocker -p 8080 --replay <file> --validate-config` checks a cassette
without serving it. Validation is strict: an unknown or misspelt field is an error, and so
is a header name with a capital letter. A future cassette format may refuse version 1
files unless its release says otherwise.

**Redaction.** `Authorization`, `Proxy-Authorization`, `Cookie` and `Set-Cookie` are always
redacted, plus whatever `--redact-header` and `--redact-query-param` name:
- the cassette and the console show `<redacted>`;
- replay does not send a redacted response header at all.

Values inside request or response **bodies are never redacted**: do not record secrets in
bodies, or edit them out by hand.

### The journal

In every mode, httpmocker keeps a journal of what it received:

```bash
curl http://localhost:8080/_httpmocker/journal              # counts and entries
curl -X DELETE http://localhost:8080/_httpmocker/journal    # reset (and rewind replay)
```

```json
{"counts": {"served": 2, "recorded": 0, "miss": 0, "exhausted": 0,
            "upstream_error": 0, "rejected": 0, "unmatched": 1},
 "dropped": 0,
 "entries": [{"seq": 1, "received_at": "2026-10-21T12:00:00.123Z",
              "request": {"method": "GET", "path": "/api/users", "query": {},
                          "headers": {"authorization": "<redacted:0c9ae6066b41>"},
                          "body_size": 0},
              "outcome": "served", "status": 200}]}
```

**Entries.** There is one entry per exchange, with one outcome:
- `served` and `unmatched` in config mode;
- `recorded` and `upstream_error` in record mode;
- `served`, `miss` and `exhausted` in replay;
- `rejected` for a reserved path, in every mode.

**Limits.** The journal keeps the last 10 000 entries. The counts are running totals since
the last reset, and `dropped` says how many entries were evicted. A request body over
64 KiB is not stored (`body_omitted`), but its `body_size` is.

**Credentials** show as `<redacted:` + the first 12 hex digits of their SHA-256 + `>`, so a
test can check which one was sent. The journal has no authentication, and anyone who can
reach the port can read it and confirm guesses of weak secrets this way. Keep a mock with
real traffic on a private network.

### TLS

Record mode checks the target's certificate, always. It trusts what the Python running
httpmocker trusts (the OpenSSL default paths of its build); `SSL_CERT_FILE` or `SSL_CERT_DIR`
override that. `SSL_CERT_FILE` *replaces* the default roots, so that file must hold every
CA the run needs. There is no option to switch verification off.

**On a host** that already trusts the target's CA, nothing needs to be set. To see where a
Python looks:

```bash
python -c "import ssl; print(ssl.get_default_verify_paths())"
```

A uv-managed CPython looks in `/etc/ssl/cert.pem` and `/etc/ssl/certs`, not necessarily
where your distribution keeps its bundle. A macOS python.org build trusts nothing until its
`Install Certificates.command` has run.

**In a container**, mount the CA file and point `SSL_CERT_FILE` at it (see the next
section). The image trusts the Debian CA bundle by default and bundles no CA of its own.

### Recording in a container

Two rules:
- **Mount the cassette's directory, not the file.** The cassette is replaced atomically
  after every interaction, which a single-file bind mount does not allow: the server
  refuses to start with `Device or resource busy`.
- **Run as yourself** (`--user "$(id -u):$(id -g)"`). The file is then yours and can be
  edited on the host; by default it belongs to the image's user (uid 999).

```bash
mkdir -p cassettes
docker run --rm -p 8080:8080 --user "$(id -u):$(id -g)" \
  -v "$(pwd)/cassettes:/app/cassettes" \
  -v "$(pwd)/ca.pem:/app/ca.pem:ro" -e SSL_CERT_FILE=/app/ca.pem \
  ghcr.io/janwychowaniak/httpmocker:0.3.0 \
  httpmocker -p 8080 --record https://api.internal.example --cassette cassettes/items.json
```

With Docker Compose, record once, then replay in the test runs:

```yaml
services:
  recorder:
    image: ghcr.io/janwychowaniak/httpmocker:0.3.0
    user: "1000:1000"            # your uid:gid, so that the cassette is yours
    command: httpmocker -p 8080 --record https://api.internal.example --cassette cassettes/items.json
    environment:
      SSL_CERT_FILE: /app/ca.pem
    volumes:
      - ./cassettes:/app/cassettes
      - ./ca.pem:/app/ca.pem:ro

  mock:
    image: ghcr.io/janwychowaniak/httpmocker:0.3.0
    command: httpmocker -p 8080 --replay cassettes/items.json
    volumes:
      - ./cassettes:/app/cassettes:ro
```

### Limitations of record and replay

- **Redirects and absolute URLs** (`Location`, links in bodies) are recorded verbatim, so on
  replay they still point at the real API, and a client that follows them leaves the mock.
- **Response bodies are not re-encoded.** A target that compresses despite
  `Accept-Encoding: identity` is recorded and replayed with its `Content-Encoding`, as
  received.
- **No egress proxy**: `HTTPS_PROXY` and `NO_PROXY` are not used; the target must be
  reachable directly.
- **No streaming**: responses are read whole and sent whole. WebSocket is not supported.
- **One cassette per recording session**: no appending, no merging, and replay never falls
  back to the target. To grow a cassette, record again.
- **Faults**: only `delay_ms`; no hangs or dropped connections.

## Project Structure

```
httpmocker/
├── httpmocker/                  # Main package
│   ├── __init__.py             # Package metadata & version
│   ├── __main__.py             # CLI entry point
│   ├── config_loader.py        # Configuration parsing & validation
│   ├── request_handler.py      # HTTP request processing & routing
│   ├── console_formatter.py    # Console logging & output
│   ├── record.py               # Record mode: forwarding, upstream client, cassette writer
│   ├── replay.py               # Replay mode: request keys and sequences
│   ├── cassette.py             # Cassette model and validation
│   ├── journal.py              # Request journal and reserved paths
│   ├── identity.py             # Request identity (the key replay matches on)
│   ├── redaction.py            # Redaction of headers and query parameters
│   ├── bodies.py               # Body representation in cassettes and the journal
│   ├── headers.py              # Header sets and helpers
│   ├── serving.py              # Send a recorded or forwarded response as given
│   └── py.typed                # PEP 561 typing marker
├── tests/                      # Unit tests, one file per module
├── configs/                    # API configuration files
│   └── example.json            # Example config (copy to your own)
├── payloads/                   # External payload files
│   ├── example.json
│   └── urls_list.json
├── cassettes/                  # Recorded conversations for --replay
│   └── example.json            # Example cassette (hand-written)
├── docs/
│   └── record-replay.md        # Contract of record, replay and the journal
├── scripts/
│   ├── gitleaks/               # Secret-scan hook scripts (wired via pre-commit)
│   ├── release/
│   │   └── archive-image.sh    # Save the published image and prove it loads back
│   └── smoke/
│       └── replay-example.sh   # Replay the example cassette in an image (CI and release)
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
├── smoke-tests.md              # curl calls for configs/example.json and cassettes/example.json
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

A short array can also go inline, as `"payload_inline": [{"id": 1}, {"id": 2}]`.

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

A `HEAD` request needs an endpoint of its own: it does not fall back to the `GET`
endpoint of the same path.

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

### Getting the Image

Pull the published image from GHCR (tagged with each release version, plus `latest`):

```bash
docker pull ghcr.io/janwychowaniak/httpmocker:0.3.0
```

On a host that cannot reach the registry, download `httpmocker-<version>-image-amd64.tar.gz`
and `SHA256SUMS` from the [release page](https://github.com/janwychowaniak/httpmocker/releases),
then verify and load the archive:

```bash
sha256sum -c SHA256SUMS --ignore-missing
docker load --input httpmocker-0.3.0-image-amd64.tar.gz
```

Or build it yourself:

```bash
docker build -t httpmocker:latest .
```

### Quick start with the published image

No clone is needed. Lay out a directory the way the repo does, with the config in
`configs/` and the payload files in `payloads/`, and mount both into the container:

```bash
mkdir -p mymock/configs mymock/payloads && cd mymock

cat > configs/config.json <<'EOF'
{
  "endpoints": [
    {
      "method": "GET",
      "path": "/api/users",
      "status": 200,
      "payload_file": "payloads/users.json",
      "delay_ms": 0
    },
    {
      "method": "POST",
      "path": "/api/login",
      "status": 401,
      "payload_inline": {"error": "invalid_credentials"},
      "delay_ms": 100
    }
  ]
}
EOF

cat > payloads/users.json <<'EOF'
[{"id": 1, "username": "alice"}, {"id": 2, "username": "bob"}]
EOF

docker run -d --name httpmocker -p 8080:8080 \
  -v $(pwd)/configs/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  ghcr.io/janwychowaniak/httpmocker:0.3.0
```

The server is up about a second later:

```bash
curl http://localhost:8080/api/users
curl -X POST http://localhost:8080/api/login -d '{"username": "test"}'
```

The request log, httpmocker's main output, is the container's output (add `-t` to
`docker run` to keep its colors):

```bash
docker logs -f httpmocker     # Ctrl+C stops following the log, not the server
```

If the container exits right after it starts, `docker logs httpmocker` says why: usually
a config error, or a file the server cannot find or read (see below). Stop and remove
the container when you are done:

```bash
docker stop httpmocker && docker rm httpmocker
```

### How the container sees your files

The image runs `httpmocker -p 8080 -c /app/config.json` in the working directory `/app`:

- **The config** is read from `/app/config.json`. Mount an existing file there: if the
  source path does not exist, Docker creates an empty directory in its place and the
  server fails with `Is a directory`.
- **Payload files** are resolved relative to `/app`, not to the config file, so
  `"payload_file": "payloads/users.json"` reads `/app/payloads/users.json`. Mount your
  payload directory at `/app/payloads` and write the paths as `payloads/<file>.json`; the
  same config then also works with the CLI started from the directory that holds
  `payloads/`. A path such as `data/users.json` would need a mount at `/app/data`.
- **The port** inside the container is always 8080; pick the host port with `-p`, for
  example `-p 9000:8080`.
- **Health**: the image checks that the server accepts connections on port 8080, so
  `docker ps` shows the container as `healthy` a few seconds after it starts. The check
  runs every 30 seconds, and three failures in a row mark the container `unhealthy`.
- **Permissions**: the server runs as an unprivileged user, so the mounted files must be
  readable by others and the directories searchable (`chmod 644` and `755`). A config
  created under a strict umask fails with `Permission denied: '/app/config.json'`, a
  payload file the server cannot read is reported as `cannot be read (Permission denied)`,
  and a payload directory it cannot enter shows up as missing payload files.

To validate a config without starting the server, run the CLI with the same mounts:

```bash
docker run --rm \
  -v $(pwd)/configs/config.json:/app/config.json:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  ghcr.io/janwychowaniak/httpmocker:0.3.0 \
  httpmocker -p 8080 -c /app/config.json --validate-config
```

To keep several configs at hand, mount the whole `configs/` directory and name the one to
use (relative to `/app`, like the payload paths):

```bash
docker run -d --name httpmocker -p 8080:8080 \
  -v $(pwd)/configs:/app/configs:ro \
  -v $(pwd)/payloads:/app/payloads:ro \
  ghcr.io/janwychowaniak/httpmocker:0.3.0 \
  httpmocker -p 8080 -c configs/staging.json
```

With Docker Compose, the quick start's container reads:

```yaml
services:
  httpmocker:
    image: ghcr.io/janwychowaniak/httpmocker:0.3.0
    ports:
      - "8080:8080"
    volumes:
      - ./configs/config.json:/app/config.json:ro
      - ./payloads:/app/payloads:ro
```

### Replaying with the published image

Mount the directory that holds the cassette and name the cassette relative to `/app`:

```bash
docker run -d --name httpmocker -p 8080:8080 \
  -v $(pwd)/cassettes:/app/cassettes:ro \
  ghcr.io/janwychowaniak/httpmocker:0.3.0 \
  httpmocker -p 8080 --replay cassettes/items.json
```

To record in a container, see [Recording in a container](#recording-in-a-container).

### Running with Docker

The examples below use the locally built `httpmocker:latest`; substitute
`ghcr.io/janwychowaniak/httpmocker:<version>` to run the published image.

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
docker compose up
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

The same install also enables secret scanning with [gitleaks](https://github.com/gitleaks/gitleaks)
on staged changes, commit messages, and pushes. It needs the `gitleaks` binary on
your `PATH` (e.g. `apt install gitleaks` or `brew install gitleaks`); without it,
those hooks print a warning and skip. CI runs the same scan on every push and pull
request.

### Manual Testing

**Local development:**
```bash
# Start the mock server
python -m httpmocker -p 8080 -c configs/example.json

# In another terminal, test with curl
curl -X GET http://localhost:8080/api/users
curl -X POST http://localhost:8080/api/login -d '{"username":"test"}'
curl -X GET http://localhost:8080/health

# Or replay the example cassette (see smoke-tests.md for its whole scenario)
python -m httpmocker -p 8080 --replay cassettes/example.json
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
