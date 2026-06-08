import argparse
import socket
import sys
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler

from bottle import run

from .config_loader import load_config
from .console_formatter import log_server_shutdown, log_server_startup
from .request_handler import create_app

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Simple HTTP REST API mocker for integration and end-to-end testing",
        prog="httpmocker",
    )

    parser.add_argument("-p", "--port", type=int, required=True, help="HTTP server port")

    parser.add_argument(
        "-c", "--config", type=str, required=True, help="Path to JSON configuration file"
    )

    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration file and exit (don't start server). Example: httpmocker -p 8080 -c config.json --validate-config",
    )

    return parser.parse_args()


def check_port_available(port: int) -> None:
    """
    Check if the specified port is available.

    Args:
        port: Port number to check

    Raises:
        SystemExit: If port is already in use
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("0.0.0.0", port))
    except OSError:
        print(f"Error: Port {port} already in use")
        sys.exit(1)


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _inject_remote_port(environ: dict[str, Any], client_address: tuple[str, int]) -> dict[str, Any]:
    """Expose the client's source port to the WSGI app.

    The stdlib ``wsgiref`` server populates ``REMOTE_ADDR`` but not
    ``REMOTE_PORT``, so without this the request log shows ``host:unknown``.
    """
    environ["REMOTE_PORT"] = str(client_address[1])
    return environ


class ClientPortRequestHandler(WSGIRequestHandler):
    """WSGI request handler that adds the client port to the environ.

    It also skips reverse-DNS lookups and silences the default request log
    (the app does its own request logging), mirroring Bottle's built-in
    ``FixedHandler``.
    """

    def get_environ(self) -> dict[str, Any]:
        return _inject_remote_port(super().get_environ(), self.client_address)

    def address_string(self) -> str:
        return str(self.client_address[0])

    def log_request(self, *args: Any, **kwargs: Any) -> None:
        pass


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def main() -> None:
    """Main entry point for httpmocker CLI application."""
    try:
        # Parse command line arguments
        args = parse_arguments()

        # Load and validate configuration
        config = load_config(args.config)

        # If only validating config, exit after successful validation
        if args.validate_config:
            print(f"✓ Configuration file '{args.config}' is valid")
            print(f"✓ Found {len(config.endpoints)} endpoint(s)")
            print("✓ All payload files exist")
            return

        # Check if port is available
        check_port_available(args.port)

        # Create Bottle application
        app = create_app(config)

        # Log startup information
        log_server_startup(args.port, args.config, len(config.endpoints))

        # Start the HTTP server
        try:
            run(
                app,
                host="0.0.0.0",
                port=args.port,
                quiet=True,  # Suppress Bottle's default logging
                handler_class=ClientPortRequestHandler,
            )
        except KeyboardInterrupt:
            pass  # Graceful shutdown
        except OSError as e:
            if "Address already in use" in str(e):
                print(f"Error: Port {args.port} already in use")
                sys.exit(1)
            else:
                print(f"Error: Could not start server: {e}")
                sys.exit(1)

        # Log shutdown
        log_server_shutdown()

    except KeyboardInterrupt:
        # Handle Ctrl+C during startup
        log_server_shutdown()
        sys.exit(0)
    except SystemExit:
        # Re-raise SystemExit from config loading
        raise
    except Exception as e:
        print(f"Error: Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
