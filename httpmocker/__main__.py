import argparse
import sys
import socket
from bottle import run, WSGIRefServer
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
            sock.bind(('localhost', port))
    except OSError:
        print(f"Error: Port {port} already in use")
        sys.exit(1)


def main():
    """Main entry point for httpmocker CLI application."""
    try:
        # Parse command line arguments
        args = parse_arguments()
        
        # Check if port is available
        check_port_available(args.port)
        
        # Load and validate configuration
        config = load_config(args.config)
        
        # Create Bottle application
        app = create_app(config)
        
        # Log startup information
        log_server_startup(args.port, args.config, len(config.endpoints))
        
        # Start the HTTP server
        try:
            run(
                app,
                host='localhost',
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
        raise
    except Exception as e:
        print(f"Error: Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
