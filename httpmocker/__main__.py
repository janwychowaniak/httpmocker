import argparse
import math
import signal
import socket
import sys
from email.message import Message
from types import FrameType
from typing import IO, TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer

from bottle import run

if TYPE_CHECKING:
    from _typeshed.wsgi import WSGIApplication

from .config_loader import load_config
from .console_formatter import log_server_shutdown, log_server_startup
from .headers import (
    DEFAULT_REDACTED_HEADERS,
    RAW_HEADERS_KEY,
    RAW_TARGET_KEY,
    TRANSPORT_HEADERS,
)
from .identity import parse_json_pointer
from .request_handler import create_app

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

DEFAULT_UPSTREAM_TIMEOUT = 30.0

# Options that only apply to --record: (attribute, option name)
_RECORD_OPTIONS = (
    ("cassette", "--cassette"),
    ("upstream_timeout", "--upstream-timeout"),
    ("ignore_query_param", "--ignore-query-param"),
    ("ignore_json_path", "--ignore-json-path"),
    ("match_header", "--match-header"),
    ("redact_header", "--redact-header"),
    ("redact_query_param", "--redact-query-param"),
)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Simple HTTP REST API mocker for integration and end-to-end testing",
        prog="httpmocker",
    )

    parser.add_argument("-p", "--port", type=int, required=True, help="HTTP server port")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-c", "--config", type=str, help="Path to JSON configuration file")
    mode.add_argument(
        "--record",
        metavar="TARGET_BASE_URL",
        help="Forward every request to this base URL and record the exchanges into --cassette",
    )
    mode.add_argument(
        "--replay", metavar="CASSETTE", help="Serve the interactions recorded in this cassette"
    )

    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate the configuration file (-c) or the cassette (--replay) and exit (don't start server). Example: httpmocker -p 8080 -c config.json --validate-config",
    )

    record = parser.add_argument_group("record mode options")
    record.add_argument(
        "--cassette", metavar="FILE", help="Cassette to record into; must be absent or empty"
    )
    record.add_argument(
        "--upstream-timeout",
        type=float,
        metavar="SECONDS",
        help=f"Deadline for each whole exchange with the target (default: {DEFAULT_UPSTREAM_TIMEOUT:g})",
    )
    record.add_argument(
        "--ignore-query-param",
        action="append",
        default=[],
        metavar="NAME",
        help="Leave this query parameter out of request matching (repeatable)",
    )
    record.add_argument(
        "--ignore-json-path",
        action="append",
        default=[],
        metavar="POINTER",
        help="Delete this JSON Pointer from JSON request bodies before matching (repeatable)",
    )
    record.add_argument(
        "--match-header",
        action="append",
        default=[],
        metavar="NAME",
        help="Make this request header part of request matching (repeatable)",
    )
    record.add_argument(
        "--redact-header",
        action="append",
        default=[],
        metavar="NAME",
        help="Hide this header's value in the cassette, console and journal; Authorization, Proxy-Authorization, Cookie and Set-Cookie always are (repeatable)",
    )
    record.add_argument(
        "--redact-query-param",
        action="append",
        default=[],
        metavar="NAME",
        help="Hide this query parameter's value in the cassette, console and journal (repeatable)",
    )

    args = parser.parse_args()
    _check_mode_options(parser, args)
    return args


def _check_mode_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """
    Check the options that depend on the run mode and normalize those of --record.

    Exits through ``parser.error`` (status 2) on a usage error.
    """
    if args.record is None:
        given = [
            option
            for attribute, option in _RECORD_OPTIONS
            if getattr(args, attribute) not in (None, [])
        ]
        if given:
            parser.error(f"{', '.join(given)}: only valid with --record")
        return

    if args.cassette is None:
        parser.error("--record requires --cassette")
    if args.validate_config:
        parser.error("--validate-config: only valid with -c or --replay")

    try:
        args.record = normalize_target_url(args.record)
    except ValueError as e:
        parser.error(str(e))

    if args.upstream_timeout is None:
        args.upstream_timeout = DEFAULT_UPSTREAM_TIMEOUT
    elif not (math.isfinite(args.upstream_timeout) and args.upstream_timeout > 0):
        parser.error(f"--upstream-timeout must be a positive number, got: {args.upstream_timeout}")

    for pointer in args.ignore_json_path:
        try:
            parse_json_pointer(pointer)
        except ValueError as e:
            parser.error(f"--ignore-json-path: {e}")

    # Header names are case-insensitive; keep them lower-cased from here on
    args.match_header = [name.lower() for name in args.match_header]
    args.redact_header = [name.lower() for name in args.redact_header]

    redacted = set(DEFAULT_REDACTED_HEADERS) | set(args.redact_header)
    for name in args.match_header:
        if name in redacted:
            parser.error(
                f"--match-header {name}: a redacted header cannot take part in matching,"
                " since its value is not kept"
            )
        if name in TRANSPORT_HEADERS:
            parser.error(
                f"--match-header {name}: a transport header cannot take part in matching,"
                " since it is not recorded"
            )


def normalize_target_url(url: str) -> str:
    """
    Check the --record target and strip a trailing slash.

    Args:
        url: Target base URL, ``http(s)://host[:port][/prefix]``

    Returns:
        The URL without a trailing slash

    Raises:
        ValueError: If the URL is not a plain http(s) base URL. The message does
            not repeat a URL that holds user info, so a password stays off the
            console.
    """
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ValueError(
            "--record target must not contain user info (user:password@): it would be"
            " written to the cassette in clear"
        )
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"--record target must be an http:// or https:// URL, got: {url!r}")
    if not parts.hostname:
        raise ValueError(f"--record target has no host, got: {url!r}")
    try:
        parts.port  # noqa: B018 - raises ValueError for a port out of range
    except ValueError:
        raise ValueError(f"--record target has an invalid port, got: {url!r}") from None
    if "?" in url or "#" in url:
        raise ValueError(
            f"--record target must not contain a query or a fragment, got: {url!r}; the"
            " request's own query is appended to it"
        )
    return url.rstrip("/")


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


def _inject_raw_request(environ: dict[str, Any], target: str, headers: Message) -> dict[str, Any]:
    """Expose the request target and headers as the client sent them.

    wsgiref percent-decodes ``PATH_INFO`` (``/a%2Fb`` arrives as ``/a/b``) and
    folds the headers into ``HTTP_*`` keys: it comma-joins repeats and turns
    ``-`` into ``_``, so ``X-Api-Key`` and ``X_Api_Key`` collide. Record mode
    forwards the request target and headers unaltered, and request matching
    needs a repeated header's values one by one, so both are kept as received:
    the target with its query, and the headers as ``(name, value)`` pairs in
    order, repeats included.
    """
    environ[RAW_TARGET_KEY] = target
    environ[RAW_HEADERS_KEY] = [(name, str(value).strip()) for name, value in headers.items()]
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

    It also adds the request target and headers as received, skips reverse-DNS
    lookups and silences the default request log (the app does its own request
    logging), mirroring Bottle's built-in ``FixedHandler``, and stops the server
    when a request is interrupted.
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
            _inject_raw_request(self.get_environ(), self.path, self.headers),
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

        # Record and replay are being built (docs/record-replay.md)
        if args.config is None:
            mode = "record" if args.record is not None else "replay"
            print(f"Error: {mode} mode is not available yet")
            sys.exit(1)

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
