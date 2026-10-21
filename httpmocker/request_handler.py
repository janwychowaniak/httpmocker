import json
import time
from typing import Any

from bottle import Bottle, request, response

from .config_loader import SUPPORTED_METHODS, Config, Endpoint, load_payload_file
from .console_formatter import (
    format_payload_source,
    log_request_received,
    log_response_matched,
    log_response_not_found,
    log_response_reserved,
    log_response_sent,
)
from .journal import (
    Journal,
    describe_request,
    install_journal,
    is_reserved_path,
    reserved_response,
    timestamp,
)
from .redaction import Redaction

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def create_app(config: Config, journal: Journal | None = None) -> Bottle:
    """
    Create and configure Bottle application with endpoints.

    Every request except those to the journal itself gets a journal entry:
    ``served``, ``unmatched``, or ``rejected`` for a path under the reserved
    prefix. The console and the journal redact the default header list.

    Args:
        config: Validated configuration object
        journal: Journal to record into; a new one when not given

    Returns:
        Configured Bottle application
    """
    app = Bottle()
    if journal is None:
        journal = Journal()
    redaction = Redaction()
    install_journal(app, journal)

    # Create endpoint lookup dictionary for fast matching
    endpoint_map: dict[tuple[str, str], Endpoint] = {}
    for endpoint in config.endpoints:
        key = (endpoint.method, endpoint.path)
        endpoint_map[key] = endpoint

    def serve(full_path: str) -> dict[str, Any] | str:
        """Log, answer and journal one request to ``full_path``."""
        received_at = timestamp()

        # Get client address for logging
        client_address = f"{request.environ.get('REMOTE_ADDR', 'unknown')}:{request.environ.get('REMOTE_PORT', 'unknown')}"

        # Log incoming request
        log_request_received(request, client_address, redaction)
        described = describe_request(request, full_path, redaction)

        if is_reserved_path(full_path):
            log_response_reserved(request.method, full_path)
            reserved = reserved_response()
            journal.add(described, received_at, "rejected", 404)
            log_response_sent()
            return reserved

        # Try to match endpoint
        endpoint = endpoint_map.get((request.method, full_path))
        if endpoint:
            served = _handle_matched_endpoint(endpoint)
            journal.add(described, received_at, "served", endpoint.status)
            return served
        unmatched = _handle_unmatched_request(request.method, full_path)
        journal.add(described, received_at, "unmatched", 404)
        return unmatched

    @app.route("/<path:path>", method=list(SUPPORTED_METHODS))
    def handle_request(path: str) -> dict[str, Any] | str:
        """Handle all HTTP requests and match against configured endpoints."""
        # Reconstruct full path with leading slash
        return serve(f"/{path}" if path else "/")

    # Handle root path separately
    @app.route("/", method=list(SUPPORTED_METHODS))
    def handle_root() -> dict[str, Any] | str:
        """Handle requests to root path."""
        return serve("/")

    return app


def _handle_matched_endpoint(endpoint: Endpoint) -> dict[str, Any] | str:
    """
    Handle matched endpoint request.

    Args:
        endpoint: Matched endpoint configuration

    Returns:
        JSON response payload
    """
    # Log successful match
    payload_source = format_payload_source(endpoint)
    log_response_matched(
        endpoint.method, endpoint.path, endpoint.status, payload_source, endpoint.delay_ms
    )

    # Apply delay if configured
    if endpoint.delay_ms > 0:
        _interruptible_delay(endpoint.delay_ms)

    # Set response status
    response.status = endpoint.status

    # Get payload (exactly one source is guaranteed by Endpoint validation)
    payload: dict[str, Any] | list[Any]
    if endpoint.payload_inline is not None:
        payload = endpoint.payload_inline
    else:
        assert endpoint.payload_file is not None
        payload = load_payload_file(endpoint.payload_file)

    # Handle HTTP semantics properly
    if endpoint.status == 204:
        # 204 No Content should not have Content-Type or body
        log_response_sent()
        return ""
    if request.method == "HEAD":
        # HEAD should return same headers as GET but no body
        response.content_type = "application/json"
        json_payload = json.dumps(payload, ensure_ascii=False)
        # Set Content-Length to what the body would be, but return empty body
        response.headers["Content-Length"] = str(len(json_payload.encode("utf-8")))
        log_response_sent()
        return ""
    # Normal response with JSON content
    response.content_type = "application/json"
    log_response_sent()
    return json.dumps(payload, ensure_ascii=False)


def _handle_unmatched_request(method: str, path: str) -> dict[str, str]:
    """
    Handle unmatched request (404).

    Args:
        method: HTTP method
        path: Request path

    Returns:
        Default 404 JSON response
    """
    log_response_not_found(method, path)

    response.status = 404
    response.content_type = "application/json"

    # Log that response is being sent
    log_response_sent()

    return {"error": "endpoint not found"}


def _interruptible_delay(delay_ms: int) -> None:
    """
    Implement interruptible delay using chunked sleep.

    Args:
        delay_ms: Total delay in milliseconds
    """
    # Convert to seconds
    total_delay = delay_ms / 1000.0
    chunk_size = 0.1  # 100ms chunks as mentioned in PRD

    elapsed = 0.0
    while elapsed < total_delay:
        sleep_time = min(chunk_size, total_delay - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time
