import json
from typing import TYPE_CHECKING, Any

from bottle import BaseRequest
from rich.console import Console
from rich.syntax import Syntax

from .config_loader import Endpoint
from .redaction import PLACEHOLDER, Redaction

if TYPE_CHECKING:
    from .cassette import Cassette

# Initialize Rich console for colored output
console = Console()


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def log_request_received(
    request: BaseRequest, client_address: str, redaction: Redaction | None = None
) -> None:
    """
    Log incoming HTTP request with full details.

    Values of redacted headers and query parameters are printed as
    ``<redacted>``; without ``redaction`` the default header list applies.

    Args:
        request: Bottle request object
        client_address: Client IP address and port
        redaction: Names whose values are hidden
    """
    if redaction is None:
        redaction = Redaction()

    console.print()  # Empty line
    console.print("~" * 70, style="cyan")
    console.print()  # Empty line
    console.print(f"Connection received on localhost {client_address}", style="bright_white")

    # Request line
    query_string = (
        f"?{redaction.query_string_of(request.query_string)}" if request.query_string else ""
    )
    console.print(f"{request.method} {request.path}{query_string} HTTP/1.1", style="bright_yellow")

    # Headers
    for header_name, header_value in request.headers.items():
        if header_name.lower() in redaction.headers:
            header_value = PLACEHOLDER
        console.print(f"{header_name:<18} {header_value}", style="white")

    # Request body (if present). Read as bytes, never through Bottle's
    # request.json: that answers a JSON body over 100 KiB with 413 and a
    # malformed one with 400, in place of the response the server would send.
    body = request.body.read()
    request.body.seek(0)  # Reset body stream for potential re-reading
    if body:
        console.print()  # Empty line
        try:
            # Try to parse as JSON for pretty printing
            _print_json_payload(json.loads(body.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not JSON, print as plain text
            console.print(body.decode("utf-8", errors="replace"), style="dim white")


def log_response_matched(
    method: str, path: str, status: int, payload_source: str, delay_ms: int
) -> None:
    """
    Log successful endpoint match and response details.

    Args:
        method: HTTP method
        path: Request path
        status: Response status code
        payload_source: Description of payload source (inline/file)
        delay_ms: Response delay in milliseconds
    """
    console.print()
    console.print("-" * 70, style="dim cyan")
    console.print()

    console.print(f"Response: {method} {path}", style="bright_green")
    console.print(f" - status:         {status}", style="green")
    console.print(f" - {payload_source}", style="green")
    if delay_ms > 0:
        console.print(f" - delay_ms:       {delay_ms}", style="green")
    console.print("...", style="dim yellow", end="")


def log_response_not_found(method: str, path: str) -> None:
    """
    Log unmatched request (404 response).

    Args:
        method: HTTP method
        path: Request path
    """
    console.print()
    console.print("-" * 70, style="dim cyan")
    console.print()

    console.print(f"Response: 404 (no match for {method} {path})", style="bright_red")
    console.print("...", style="dim yellow", end="")


def log_response_reserved(method: str, path: str) -> None:
    """
    Log a request under the reserved prefix (404 with the marker header).

    Args:
        method: HTTP method
        path: Request path
    """
    console.print()
    console.print("-" * 70, style="dim cyan")
    console.print()

    console.print(
        f"Response: 404 ({method} {path} is under the reserved prefix /_httpmocker)",
        style="bright_red",
    )
    console.print("...", style="dim yellow", end="")


def log_response_sent() -> None:
    """Log that response has been sent successfully."""
    console.print("sent", style="bright_green")


def log_server_startup(port: int, config_file: str, endpoint_count: int) -> None:
    """
    Log server startup information.

    Args:
        port: Server port
        config_file: Configuration file path
        endpoint_count: Number of configured endpoints
    """
    console.print()
    console.print("=" * 70, style="bright_blue")
    console.print("httpmocker starting up", style="bright_blue bold")
    console.print("=" * 70, style="bright_blue")
    console.print(f"Port:        {port}", style="blue")
    console.print(f"Config:      {config_file}", style="blue")
    console.print(f"Endpoints:   {endpoint_count}", style="blue")
    console.print("=" * 70, style="bright_blue")
    console.print("Press Ctrl+C to stop", style="blue")
    console.print()


def log_replay_startup(port: int, cassette_file: str, cassette: "Cassette") -> None:
    """
    Log replay startup: the cassette and the lists it was recorded with.

    Args:
        port: Server port
        cassette_file: Cassette file path
        cassette: The loaded cassette
    """

    def listed(names: list[str]) -> str:
        return ", ".join(names) if names else "-"

    console.print()
    console.print("=" * 70, style="bright_blue")
    console.print("httpmocker starting up (replay)", style="bright_blue bold")
    console.print("=" * 70, style="bright_blue")
    console.print(f"Port:                   {port}", style="blue")
    console.print(f"Cassette:               {cassette_file}", style="blue")
    console.print(f"Interactions:           {len(cassette.interactions)}", style="blue")
    if cassette.target:
        console.print(f"Recorded from:          {cassette.target}", style="blue")
    console.print(
        f"Ignored query params:   {listed(cassette.identity.ignore_query_params)}", style="blue"
    )
    console.print(
        f"Ignored JSON paths:     {listed(cassette.identity.ignore_json_paths)}", style="blue"
    )
    console.print(
        f"Matched headers:        {listed(cassette.identity.match_headers)}", style="blue"
    )
    console.print(f"Redacted headers:       {listed(cassette.redacted_headers)}", style="blue")
    console.print(f"Redacted query params:  {listed(cassette.redacted_query_params)}", style="blue")
    console.print("=" * 70, style="bright_blue")
    console.print("Press Ctrl+C to stop", style="blue")
    console.print()


def log_replay_served(
    method: str, path: str, status: int, index: int, position: int, total: int, delay_ms: int
) -> None:
    """
    Log a request answered from the cassette.

    Args:
        method: HTTP method
        path: Request path
        status: Recorded status sent
        index: 0-based index of the interaction in the cassette
        position: Which response of the request's sequence this is, from 1
        total: Length of the request's sequence
        delay_ms: Delay applied before answering
    """
    console.print()
    console.print("-" * 70, style="dim cyan")
    console.print()

    console.print(
        f"Response: {method} {path} → {status} served #{index} ({position}/{total})",
        style="bright_green",
    )
    if delay_ms > 0:
        console.print(f" - delay_ms:       {delay_ms}", style="green")
    console.print("...", style="dim yellow", end="")


def log_replay_unanswered(outcome: str, method: str, path: str, total: int) -> None:
    """
    Log a request the cassette has no response for, so that it stands out.

    Args:
        outcome: "miss" (no interaction for the request) or "exhausted" (all used)
        method: HTTP method
        path: Request path
        total: Length of the request's sequence (exhausted)
    """
    console.print()
    console.print("-" * 70, style="dim cyan")
    console.print()

    if outcome == "miss":
        reason = "no recorded interaction for this request"
    else:
        reason = f"all {total} recorded response(s) for this request were used"
    console.print(f"{outcome.upper()}: {method} {path} → 500, {reason}", style="bold white on red")
    console.print("...", style="dim yellow", end="")


def log_server_shutdown() -> None:
    """Log server shutdown message."""
    console.print()
    console.print("=" * 70, style="bright_blue")
    console.print("httpmocker shutting down", style="bright_blue bold")
    console.print("=" * 70, style="bright_blue")


def _print_json_payload(data: Any) -> None:
    """
    Pretty-print JSON data with syntax highlighting.

    Args:
        data: JSON-serializable data to print
    """
    try:
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        syntax = Syntax(json_str, "json", theme="monokai", line_numbers=False)
        console.print(syntax)
    except (TypeError, ValueError):
        # Fallback to plain text if JSON serialization fails
        console.print(str(data), style="dim white")


def format_payload_source(endpoint: Endpoint) -> str:
    """
    Format payload source description for logging.

    Args:
        endpoint: Endpoint configuration object

    Returns:
        Formatted payload source string
    """
    if endpoint.payload_inline is not None:
        # Show truncated inline payload for brevity
        payload_str = json.dumps(endpoint.payload_inline, separators=(",", ":"))
        if len(payload_str) > 50:
            payload_str = payload_str[:47] + "..."
        return f"payload_inline: {payload_str}"
    return f"payload_file:   {endpoint.payload_file}"
