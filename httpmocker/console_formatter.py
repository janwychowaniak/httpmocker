import json
from typing import Dict, Any, Optional
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text
from bottle import BaseRequest


# Initialize Rich console for colored output
console = Console()


def log_request_received(request: BaseRequest, client_address: str) -> None:
    """
    Log incoming HTTP request with full details.
    
    Args:
        request: Bottle request object
        client_address: Client IP address and port
    """
    console.print("~" * 70, style="cyan")
    console.print(f"Connection received on localhost {client_address}", style="bright_white")
    
    # Request line
    query_string = f"?{request.query_string}" if request.query_string else ""
    console.print(f"{request.method} {request.path}{query_string} HTTP/1.1", style="bright_yellow")
    
    # Headers
    for header_name, header_value in request.headers.items():
        console.print(f"{header_name:<12} {header_value}", style="white")
    
    # Request body (if present)
    if hasattr(request, 'json') and request.json:
        console.print()  # Empty line
        _print_json_payload(request.json)
    elif request.body.read():
        console.print()  # Empty line
        try:
            # Try to parse as JSON for pretty printing
            body_data = json.loads(request.body.read().decode('utf-8'))
            _print_json_payload(body_data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not JSON, print as plain text
            console.print(request.body.read().decode('utf-8', errors='replace'), style="dim white")
        # Reset body stream for potential re-reading
        request.body.seek(0)


def log_response_matched(method: str, path: str, status: int, payload_source: str, delay_ms: int) -> None:
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
    console.print(f" - delay_ms:       {delay_ms}", style="green")


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
    console.print("Press Ctrl+C to stop", style="dim blue")
    console.print()


def log_server_shutdown() -> None:
    """Log server shutdown message."""
    console.print()
    console.print("=" * 70, style="bright_blue")
    console.print("httpmocker shutting down", style="bright_blue bold")
    console.print("=" * 70, style="bright_blue")


def log_delay_start(delay_ms: int) -> None:
    """
    Log delay start message.
    
    Args:
        delay_ms: Delay duration in milliseconds
    """
    if delay_ms > 0:
        console.print(f"Delaying response by {delay_ms}ms...", style="dim yellow")


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


def format_payload_source(endpoint) -> str:
    """
    Format payload source description for logging.
    
    Args:
        endpoint: Endpoint configuration object
        
    Returns:
        Formatted payload source string
    """
    if endpoint.payload_inline is not None:
        # Show truncated inline payload for brevity
        payload_str = json.dumps(endpoint.payload_inline, separators=(',', ':'))
        if len(payload_str) > 50:
            payload_str = payload_str[:47] + "..."
        return f"payload_inline: {payload_str}"
    else:
        return f"payload_file:   {endpoint.payload_file}"
