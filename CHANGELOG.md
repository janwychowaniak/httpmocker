# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-21

`ghcr.io/janwychowaniak/httpmocker@sha256:99d8168c86f2cc9f189ab66985df33a7270b0c2a42deba2087fd4aa0f099703d`

Image Id `sha256:0c144848db9bb9de93a3aef33669ec5e4ce0feaed69d1d39a528fcad023bebcc`, after a
pull and after `docker load` of the release file alike. Python 3.14.8, on the same base image
as 0.2.0 (Debian 13). The digest was read from the workflow's push and from a pull of the
tag; the image can be pulled without logging in. The release files pass
`sha256sum -c SHA256SUMS`, and the release body equals the tag message.

On the published image:
- `scripts/smoke/replay-example.sh` passes.
- Recording through it from a replay of the example cassette, then replaying the new
  cassette, gives identical answers over the whole scenario. The secret header value
  appears in neither the cassette nor the log.
- Recording through TLS works with a CA mounted for `SSL_CERT_FILE` and answers `502`
  without it.
- A cassette directory the image's user cannot write is refused at start.
- Config mode reserves `/_httpmocker` and journals `served`, `rejected` and `unmatched`.

The release workflow's new step, the replay of the example before the push, ran for the
first time on this tag and passed, as did `actions/upload-artifact` 7.0.2 and
`actions/download-artifact` 8.0.2.

Record and replay, for APIs that keep state. A config answers every request the same way,
while a real API answers the second identical `PUT` with a conflict. httpmocker can now
record a conversation with the real API and replay it, error bodies included. The contract
is `docs/record-replay.md`; the README's "Record and replay" section is the guide.

### Added
- Record mode: `httpmocker -p PORT --record TARGET_BASE_URL --cassette FILE` forwards
  every request to the target and returns its response. Each exchange is appended to the
  cassette, which is rewritten atomically, so the file on disk is complete after every
  interaction.
  - Requests are forwarded as received: the raw path and query, headers in order with
    repeats, and the body. Bodies are asked for without compression.
  - Credentials are redacted in the cassette: `Authorization`, `Proxy-Authorization`,
    `Cookie` and `Set-Cookie`, plus `--redact-header` and `--redact-query-param`.
  - `--ignore-query-param`, `--ignore-json-path` and `--match-header` set the request
    identity the cassette is replayed with.
  - `--upstream-timeout` (default 30 s) bounds each whole exchange. A target that fails
    or misses the deadline gets `502` with `X-Httpmocker: upstream-error`, and nothing is
    recorded.
  - TLS verification is always on and uses the system trust store, which `SSL_CERT_FILE`
    / `SSL_CERT_DIR` override.
  - A cassette that already holds interactions is never overwritten.
- Replay mode: `httpmocker -p PORT --replay CASSETTE` serves a cassette, a JSON file of
  recorded interactions.
  - Each request is matched by method, path, query and body, with a JSON body compared in
    canonical form. The cassette's lists can leave volatile query parameters and JSON
    paths out of matching, or add headers to it.
  - Repeated identical requests get the recorded responses in recorded order.
  - A request with no recorded interaction, or one past the end of its sequence, gets
    `500` with `X-Httpmocker: miss` or `exhausted` and its key, and a journal entry.
  - `--replay CASSETTE --validate-config` checks a cassette without serving it, strictly:
    an unknown field is an error. `cassettes/example.json` shows the format.
- A request journal in every mode, for tests to assert on what the server received.
  - `GET /_httpmocker/journal` returns per-outcome counts and the entries: method, path,
    query, headers, body, outcome and status of each exchange.
  - `DELETE /_httpmocker/journal` resets it and rewinds every replay sequence.
  - It keeps the last 10 000 entries, with counts as running totals and a `dropped`
    count, and omits request bodies over 64 KiB.
- Documentation: the README's "Record and replay" section covers recording in a container
  (directory mount, `--user`, a CA mounted for `SSL_CERT_FILE`) and a Compose example.
  `smoke-tests.md` gains the replay scenario. CI and the release workflow replay the
  example cassette in the Docker image (`scripts/smoke/replay-example.sh`).

### Changed
- The command line takes exactly one of `-c`, `--record` and `--replay`; with `-c` it
  behaves as before.
- Paths under `/_httpmocker` are reserved for the server: a config endpoint there fails
  validation, and a request there other than the journal gets `404` with
  `X-Httpmocker: reserved`.
- The console prints the values of `Authorization`, `Proxy-Authorization`, `Cookie` and
  `Set-Cookie` as `<redacted>`; the journal shows them as `<redacted:` + the first 12 hex
  digits of their SHA-256 + `>`, so a test can still tell which credential was sent.

### Fixed
- A request whose JSON body was over 100 KiB got Bottle's `413` page instead of the
  configured response, and one whose body was declared `application/json` but did not
  parse got `400`. Printing the body on the console went through Bottle's JSON parser,
  which enforces both; the body is now read as bytes.

## [0.2.0] - 2026-10-14

`ghcr.io/janwychowaniak/httpmocker@sha256:d4761f9827cac4c5549d45056833352ebaf58b6ea4e3e47a38fe4b40cc473706`

Image Id `sha256:5927b7d000450d0e541cc8ff272fa6d9ad12d6be065a8caecdf87e40dcfe1e49`, after a
pull and after `docker load` of the release file alike. Python 3.14.8, on the same base
image as 0.1.1. The digest was read from the workflow's push and from a pull of the tag; the
image can be pulled without logging in. On the published image an inline array is served,
a config with an unsupported method and a path with `?` fails `--validate-config` with exit
code 1, the container turns `healthy`, and `docker stop` ends the server in about 0.4
seconds with exit code 0.

The release notes say every problem is reported at once. That holds within each of two
passes, as the Changed entry below now says: a config with both kinds of errors shows its
payload file problems only on the run after its fields are fixed.

### Added
- `payload_inline` accepts a top-level JSON array as well as an object, as
  `payload_file` already did, so a short list no longer needs a file of its own.
  Every existing config stays valid; an inline value that is neither an object nor
  an array is still rejected.

### Changed
- Config validation, at startup and with `--validate-config`, now rejects endpoints
  the server could never serve, where it used to accept them silently:
  - a method other than GET, POST, PUT, DELETE, PATCH, HEAD and OPTIONS, the ones the
    server routes (a request with it got Bottle's 405 HTML page);
  - a path that does not start with `/`, which no request path matches;
  - a path with `?` or `#`: matching ignores the query string and clients do not send
    the fragment, so `/api/users?filter=active` answered only a request that encoded
    the `?` as `%3F`, never `/api/users?filter=active` itself;
  - a payload file that is not valid JSON, not UTF-8 or cannot be read (every request
    to it got a 500); before, only its existence was checked.

  Problems are reported in two passes, each listing all it finds: the endpoints' fields
  first, then, once those pass, the payload files (a file shared by several endpoints
  only once). A config this rejects had endpoints that could never respond as
  configured.
- `docker-compose.example.yml` drops the obsolete `version` key, for which
  `docker compose` printed a warning on every run.

### Fixed
- The README and CLAUDE.md said a `HEAD` request gets the headers of the `GET` endpoint
  of its path; it matches only an endpoint configured with `HEAD`, and they now say so.
  They also said every response is JSON, while the `405` page for an unsupported method
  and the `500` page for a payload file that breaks at run time are not; the README's
  Limitations now name both. The README also describes the image's health check, and
  both list `smoke-tests.md` in the project structure.

## [0.1.1] - 2026-10-14

`ghcr.io/janwychowaniak/httpmocker@sha256:e23073c2adf77d3fd84e362af3fad1005fc83fb7c79d713d8e3a8d8e09df42b4`

Image Id `sha256:2032379dbcb0d68ca4525e6d61d26d31d4a0230fd87de7a50c90b9471be32cde`, after a
pull and after `docker load` of the release file alike. Python 3.14.8, on the same base
image as 0.1.0. The digest was read from the workflow's push and from a pull of the tag; the
image can be pulled without logging in. On the published image `docker stop` ends the server
in about 0.3 seconds with exit code 0, idle or in the middle of a response delay.

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
  config that no longer exists. This change alone leaves the image as it was.

### Fixed
- `docker stop` now shuts the server down cleanly, as Ctrl+C does. The image runs
  the server as PID 1, where a SIGTERM without a handler is ignored, so Docker
  waited out its 10-second timeout and killed the process (exit code 137); outside
  a container SIGTERM ended it at once, without the shutdown message. SIGTERM now
  stops the server with exit code 0, which also covers systemd and Kubernetes.
- Ctrl+C or SIGTERM during a response delay now stops the server at once. The
  wsgiref server answered the interrupted request with a 500 the config never
  defined, logged a traceback and kept serving until a second signal, so a
  `docker stop` in the middle of a delay still ended in a kill after 10 seconds.
  The interrupted connection is now closed without a response.
- Restarting the server on the same port no longer fails with "Port … already in
  use" for up to a minute after it served a request. The port check before startup
  bound without `SO_REUSEADDR`, so the closed connections the stopped server had
  left in TIME_WAIT made the port look busy, although the server itself binds over
  them. A port another process listens on is still reported.

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
