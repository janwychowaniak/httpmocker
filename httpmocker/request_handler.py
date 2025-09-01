import time
import json
from typing import Dict, Any, Union
from bottle import Bottle, request, response
from .config_loader import Config, Endpoint, load_payload_file
from .console_formatter import (
    log_request_received,
    log_response_matched,
    log_response_not_found,
    log_response_sent,
    format_payload_source
)


def create_app(config: Config) -> Bottle:
    """
    Create and configure Bottle application with endpoints.

    Args:
        config: Validated configuration object

    Returns:
        Configured Bottle application
    """
    app = Bottle()

    # Create endpoint lookup dictionary for fast matching
    endpoint_map = {}
    for endpoint in config.endpoints:
        key = (endpoint.method, endpoint.path)
        endpoint_map[key] = endpoint

    @app.route('/<path:path>', method=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
    def handle_request(path):
        """Handle all HTTP requests and match against configured endpoints."""
        # Reconstruct full path with leading slash
        full_path = f"/{path}" if path else "/"

        # Get client address for logging
        client_address = f"{request.environ.get('REMOTE_ADDR', 'unknown')}:{request.environ.get('REMOTE_PORT', 'unknown')}"

        # Log incoming request
        log_request_received(request, client_address)

        # Try to match endpoint
        endpoint_key = (request.method, full_path)
        endpoint = endpoint_map.get(endpoint_key)

        if endpoint:
            return _handle_matched_endpoint(endpoint)
        else:
            return _handle_unmatched_request(request.method, full_path)

    # Handle root path separately
    @app.route('/', method=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'])
    def handle_root():
        """Handle requests to root path."""
        client_address = f"{request.environ.get('REMOTE_ADDR', 'unknown')}:{request.environ.get('REMOTE_PORT', 'unknown')}"
        log_request_received(request, client_address)

        endpoint_key = (request.method, "/")
        endpoint = endpoint_map.get(endpoint_key)

        if endpoint:
            return _handle_matched_endpoint(endpoint)
        else:
            return _handle_unmatched_request(request.method, "/")

    return app


def _handle_matched_endpoint(endpoint: Endpoint) -> Union[Dict[str, Any], str]:
    """
    Handle matched endpoint request.

    Args:
        endpoint: Matched endpoint configuration

    Returns:
        JSON response payload
    """
    # Log successful match
    payload_source = format_payload_source(endpoint)
    log_response_matched(endpoint.method, endpoint.path, endpoint.status,
                         payload_source, endpoint.delay_ms)

    # Apply delay if configured
    if endpoint.delay_ms > 0:
        _interruptible_delay(endpoint.delay_ms)

    # Set response status
    response.status = endpoint.status

    # Get payload
    if endpoint.payload_inline is not None:
        payload = endpoint.payload_inline
    else:
        payload = load_payload_file(endpoint.payload_file)

    # Handle HTTP semantics properly
    if endpoint.status == 204:
        # 204 No Content should not have Content-Type or body
        log_response_sent()
        return ""
    elif request.method == "HEAD":
        # HEAD should return same headers as GET but no body
        response.content_type = 'application/json'
        json_payload = json.dumps(payload, ensure_ascii=False)
        # Set Content-Length to what the body would be, but return empty body
        response.headers['Content-Length'] = str(len(json_payload.encode('utf-8')))
        log_response_sent()
        return ""
    else:
        # Normal response with JSON content
        response.content_type = 'application/json'
        log_response_sent()
        return json.dumps(payload, ensure_ascii=False)


def _handle_unmatched_request(method: str, path: str) -> Dict[str, str]:
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
    response.content_type = 'application/json'

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
