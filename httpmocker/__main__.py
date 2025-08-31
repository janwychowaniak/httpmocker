import argparse
import sys
import socket
from bottle import run
from .config_loader import load_config
from .request_handler import create_app
from .console_formatter import log_server_startup, log_server_shutdown


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Simple HTTP REST API mocker for integration and end-to-end testing",
        prog="httpmocker"
    )

    parser.add_argument(
        "-p", "--port",
        type=int,
        required=True,
        help="HTTP server port"
    )

    parser.add_argument(
        "-c", "--config",
        type=str,
        required=True,
        help="Path to JSON configuration file"
    )

    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration file and exit (don't start server). Example: httpmocker -p 8080 -c config.json --validate-config"
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
            sock.bind(('0.0.0.0', port))
    except OSError:
        print(f"Error: Port {port} already in use")
        sys.exit(1)


def main():
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
                host='0.0.0.0',
                port=args.port,
                quiet=True  # Suppress Bottle's default logging
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
        raise  # pylint: disable=try-except-raise
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Error: Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
