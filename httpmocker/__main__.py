import argparse
import signal
import socket
import sys
from types import FrameType
from typing import IO, TYPE_CHECKING, Any, cast
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer

from bottle import run

if TYPE_CHECKING:
    from _typeshed.wsgi import WSGIApplication

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
            # Bind as the server does, with SO_REUSEADDR: the connections a
            # stopped server leaves in TIME_WAIT for a minute do not make the
            # port busy. On Windows SO_REUSEADDR would bind even over a
            # listening socket, so the check would miss a real conflict.
            if sys.platform != "win32":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
    except OSError:
        print(f"Error: Port {port} already in use")
        sys.exit(1)


def _shutdown_on_sigterm(signum: int, frame: FrameType | None) -> None:
    """Turn SIGTERM into the same clean shutdown as Ctrl+C.

    ``docker stop``, systemd and Kubernetes stop a process with SIGTERM. In a
    container the server runs as PID 1, where a signal without a handler is
    ignored, so Docker would wait out its timeout and kill the process.
    """
    raise KeyboardInterrupt


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _inject_remote_port(environ: dict[str, Any], client_address: tuple[str, int]) -> dict[str, Any]:
    """Expose the client's source port to the WSGI app.

    The stdlib ``wsgiref`` server populates ``REMOTE_ADDR`` but not
    ``REMOTE_PORT``, so without this the request log shows ``host:unknown``.
    """
    environ["REMOTE_PORT"] = str(client_address[1])
    return environ


class _InterruptibleServerHandler(ServerHandler):
    """A wsgiref handler that lets Ctrl+C and SIGTERM through.

    wsgiref answers any exception from the app, KeyboardInterrupt included,
    with a 500 and keeps serving. A signal that arrived during a response delay
    would then send a response the config never defined and leave the server
    running, so this handler only records the interrupt.
    """

    interrupted = False
    request_handler: WSGIRequestHandler  # set by the request handler; not in typeshed

    def handle_error(self) -> None:
        if isinstance(sys.exc_info()[1], KeyboardInterrupt):
            self.interrupted = True
            return
        super().handle_error()


class ClientPortRequestHandler(WSGIRequestHandler):
    """WSGI request handler that adds the client port to the environ.

    It also skips reverse-DNS lookups and silences the default request log
    (the app does its own request logging), mirroring Bottle's built-in
    ``FixedHandler``, and stops the server when a request is interrupted.
    """

    server: WSGIServer  # typeshed has the BaseServer of any request handler

    def handle(self) -> None:
        """Handle one request; re-raise an interrupt once wsgiref is done with it.

        This is ``WSGIRequestHandler.handle`` (the same in Python 3.10 to 3.14)
        with ``_InterruptibleServerHandler`` in place of ``ServerHandler``. The
        interrupted connection is closed without a response.
        """
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = ""
            self.request_version = ""
            self.command = ""
            self.send_error(414)
            return

        if not self.parse_request():  # An error code has been sent, just exit
            return

        handler = _InterruptibleServerHandler(
            self.rfile,
            cast("IO[bytes]", self.wfile),
            self.get_stderr(),
            self.get_environ(),
            multithread=False,
        )
        handler.request_handler = self  # backpointer for logging
        handler.run(cast("WSGIApplication", self.server.get_app()))
        if handler.interrupted:
            raise KeyboardInterrupt

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
            print("✓ All payload files exist and hold valid JSON")
            return

        # Check if port is available
        check_port_available(args.port)

        # Create Bottle application
        app = create_app(config)

        # Log startup information
        log_server_startup(args.port, args.config, len(config.endpoints))

        # Stop on SIGTERM exactly as on Ctrl+C
        signal.signal(signal.SIGTERM, _shutdown_on_sigterm)

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
