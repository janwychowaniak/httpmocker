import base64
import contextlib
import http.client
import json
import os
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from bottle import BaseRequest, Bottle, request, response

from . import __version__
from .bodies import represent_body
from .cassette import CASSETTE_FORMAT
from .config_loader import SUPPORTED_METHODS
from .console_formatter import (
    log_record_recorded,
    log_record_upstream_error,
    log_request_received,
    log_response_reserved,
    log_response_sent,
)
from .headers import (
    DEFAULT_REDACTED_HEADERS,
    RAW_TARGET_KEY,
    header_mapping,
    request_header_pairs,
    transport_header_names,
)
from .identity import parse_query
from .journal import (
    MARKER_HEADER,
    Journal,
    describe_request,
    install_journal,
    is_reserved_path,
    reserved_response,
    timestamp,
)
from .redaction import Redaction, console_placeholder
from .serving import send_as_given, without_default_content_type

if TYPE_CHECKING:
    from _typeshed.wsgi import WSGIApplication

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# Characters a request path keeps unencoded when it is rebuilt from the decoded
# path (only under a WSGI server that does not pass the raw target on)
_PATH_SAFE = "/:@!$&'()*+,;=-._~"

# Methods for which http.client sends Content-Length: 0 without a body
_METHODS_EXPECTING_BODY = ("POST", "PUT", "PATCH")


@dataclass(frozen=True)
class RecordSettings:
    """What --record was given (docs/record-replay.md §3, §6)."""

    target: str
    cassette: str
    upstream_timeout: float
    ignore_query_params: tuple[str, ...] = ()
    ignore_json_paths: tuple[str, ...] = ()
    match_headers: tuple[str, ...] = ()
    redact_headers: tuple[str, ...] = ()
    redact_query_params: tuple[str, ...] = ()

    @property
    def redacted_headers(self) -> list[str]:
        """The four default headers, then those added with --redact-header."""
        return list(dict.fromkeys([*DEFAULT_REDACTED_HEADERS, *self.redact_headers]))

    @property
    def redaction(self) -> Redaction:
        return Redaction(
            headers=frozenset(self.redacted_headers),
            query_params=frozenset(self.redact_query_params),
        )

    def cassette_document(self) -> dict[str, Any]:
        """A cassette with no interactions yet, as written before the server listens."""
        return {
            "httpmocker_cassette": CASSETTE_FORMAT,
            "tool_version": __version__,
            "recorded_at": timestamp(),
            "target": self.target,
            "identity": {
                "ignore_query_params": list(self.ignore_query_params),
                "ignore_json_paths": list(self.ignore_json_paths),
                "match_headers": list(self.match_headers),
            },
            "redacted_headers": self.redacted_headers,
            "redacted_query_params": list(self.redact_query_params),
            "interactions": [],
        }


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def check_cassette_path(path: str) -> None:
    """
    Ensure --record may write to ``path``: no file, an empty file, or a
    cassette with no interactions (as a session stopped before its first
    request leaves it). A cassette is never overwritten or appended to.

    Raises:
        ValueError: With the reason the path is refused
    """
    if not os.path.exists(path):
        return
    if os.path.isdir(path):
        raise ValueError(f"Cassette path is a directory: {path}")
    if os.path.getsize(path) == 0:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    if (
        isinstance(data, dict)
        and data.get("httpmocker_cassette") == CASSETTE_FORMAT
        and data.get("interactions") == []
    ):
        return
    raise ValueError(
        f"Cassette file already exists and is not empty: {path}; record into a new file or"
        " remove it first (a cassette is never overwritten or appended to)"
    )


class CassetteWriter:
    """Keeps the cassette on disk complete after every interaction.

    The whole file is rewritten each time into a temp file in the same
    directory, synced and moved over the cassette with ``os.replace``, so the
    file is never half-written. (A cassette mounted into a container as a
    single file cannot be replaced this way: mount its directory.)
    """

    def __init__(self, path: str, document: dict[str, Any]) -> None:
        self.path = path
        self._document = document

    def start(self) -> None:
        """Write the cassette with no interactions."""
        self._write()

    def append(self, interaction: dict[str, Any]) -> int:
        """
        Add an interaction and rewrite the cassette.

        Returns:
            The 0-based index of the interaction
        """
        interactions: list[dict[str, Any]] = self._document["interactions"]
        interactions.append(interaction)
        self._write()
        return len(interactions) - 1

    def _write(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        temp = os.path.join(directory, f".{os.path.basename(self.path)}.{os.getpid()}.tmp")
        # 0o666 before the umask, as a file opened for writing gets it
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._document, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class UpstreamError(Exception):
    """The target could not be reached or gave no complete response in time."""


@dataclass(frozen=True)
class UpstreamResponse:
    status: int
    headers: list[tuple[str, str]]
    body: bytes
    duration_ms: int


class Upstream:
    """The target of --record, reached through http.client.

    TLS uses ``ssl.create_default_context()``: the system trust store, which
    ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` override; verification is always on.
    """

    def __init__(self, target: str, timeout: float) -> None:
        parts = urlsplit(target)
        self._https = parts.scheme.lower() == "https"
        self._host = parts.hostname or ""
        self._port = parts.port
        self._host_header = parts.netloc
        self._prefix = parts.path
        self._timeout = timeout
        self._context = ssl.create_default_context() if self._https else None

    def exchange(
        self, method: str, target: str, headers: list[tuple[str, str]], body: bytes
    ) -> UpstreamResponse:
        """
        Send a request and read the whole response within the deadline.

        The deadline runs from the start of the connect to the last byte of the
        body. The connect gets the whole deadline as its timeout; from then on a
        watchdog shuts the socket down when the deadline passes, so a target
        that keeps sending a little at a time cannot stretch the exchange.

        Args:
            method: HTTP method
            target: Request target as received (path and query)
            headers: Headers to send, without Host and Content-Length
            body: Body bytes

        Returns:
            Status, headers and body of the response, and how long it took

        Raises:
            UpstreamError: With a cause fit for the client and the console
        """
        started = time.monotonic()
        expired = threading.Event()
        watchdog: threading.Timer | None = None

        connection: http.client.HTTPConnection
        if self._https:
            connection = http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=self._context
            )
        else:
            connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            connection.connect()
            sock = connection.sock
            assert sock is not None

            def expire() -> None:
                expired.set()
                # The plain socket's shutdown, also under TLS: a read blocked in
                # another thread then ends at once
                with contextlib.suppress(OSError):
                    socket.socket.shutdown(sock, socket.SHUT_RDWR)

            watchdog = threading.Timer(max(started + self._timeout - time.monotonic(), 0), expire)
            watchdog.daemon = True
            watchdog.start()

            connection.putrequest(
                method, self._prefix + target, skip_host=True, skip_accept_encoding=True
            )
            connection.putheader("Host", self._host_header)
            for name, value in headers:
                connection.putheader(name, value)
            if body or method in _METHODS_EXPECTING_BODY:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body or None)
            upstream = connection.getresponse()
            chunks = []
            # read1 returns what one read of the socket brings; http.client
            # closes the response once the body is complete
            while not upstream.isclosed():
                chunk = upstream.read1(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            if expired.is_set():
                # A body that ends with the connection looks complete when the
                # watchdog cuts it
                raise TimeoutError("deadline exceeded")
            return UpstreamResponse(
                upstream.status,
                upstream.getheaders(),
                b"".join(chunks),
                round((time.monotonic() - started) * 1000),
            )
        except Exception as e:
            if expired.is_set() or isinstance(e, TimeoutError):
                raise UpstreamError(
                    f"no complete response within the deadline of {self._timeout:g} s"
                ) from e
            error = _upstream_error(e, self._host, self._host_header)
            if error is e:
                raise
            raise error from e
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()


def _upstream_error(e: Exception, host: str, host_header: str) -> Exception:
    """
    Describe why an exchange with the target failed.

    Returns:
        An UpstreamError with a cause fit for the client and the console, or
        ``e`` itself when it is not a failure of the exchange
    """
    if isinstance(e, ssl.SSLCertVerificationError):
        detail = getattr(e, "verify_message", None) or e
        return UpstreamError(f"TLS certificate verification failed: {detail}")
    if isinstance(e, ssl.SSLError):
        return UpstreamError(f"TLS error: {getattr(e, 'reason', None) or e}")
    if isinstance(e, http.client.RemoteDisconnected):
        return UpstreamError("the target closed the connection without a response")
    if isinstance(e, http.client.HTTPException):
        return UpstreamError(f"invalid response from the target: {e!r}")
    if isinstance(e, ConnectionRefusedError):
        return UpstreamError(f"connection refused by {host_header}")
    if isinstance(e, socket.gaierror):
        return UpstreamError(f"cannot resolve {host}: {e.strerror}")
    if isinstance(e, OSError):
        return UpstreamError(f"{e.__class__.__name__}: {e.strerror or e}")
    return e


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def _first(pairs: list[tuple[str, str]], name: str) -> str | None:
    """The first value of a header, if present."""
    return next((value for key, value in pairs if key.lower() == name), None)


def _raw_target(live: BaseRequest, path: str) -> str:
    """The request target as received, or rebuilt from the decoded path."""
    raw = live.environ.get(RAW_TARGET_KEY)
    if isinstance(raw, str) and raw.startswith("/"):
        return raw
    query = f"?{live.query_string}" if live.query_string else ""
    return quote(path, safe=_PATH_SAFE) + query


def _recorded_response_body(upstream: UpstreamResponse) -> dict[str, Any]:
    """The response body as the cassette keeps it; an encoded body stays as received."""
    encoding = _first(upstream.headers, "content-encoding")
    if encoding is not None and encoding.strip().lower() != "identity":
        return (
            {"body_base64": base64.b64encode(upstream.body).decode("ascii")}
            if upstream.body
            else {}
        )
    return represent_body(upstream.body, _first(upstream.headers, "content-type"))


def create_record_app(
    settings: RecordSettings,
    writer: CassetteWriter,
    journal: Journal | None = None,
    upstream: Upstream | None = None,
) -> "WSGIApplication":
    """
    Create the WSGI app of record mode (docs/record-replay.md §6).

    Every request except those to the journal is forwarded to the target, its
    response returned to the client and, redacted, appended to the cassette:
    journal outcome ``recorded``. A target that fails gets ``502`` with the
    marker (``upstream_error``) and nothing is recorded; a path under the
    reserved prefix gets ``404`` (``rejected``) and is never forwarded.

    Args:
        settings: The --record options
        writer: Writer of the cassette, already started
        journal: Journal to record into; a new one when not given
        upstream: Client of the target; one for ``settings.target`` when not given

    Returns:
        The WSGI application
    """
    app = Bottle()
    if journal is None:
        journal = Journal()
    if upstream is None:
        upstream = Upstream(settings.target, settings.upstream_timeout)
    install_journal(app, journal)
    redaction = settings.redaction

    def serve(full_path: str) -> dict[str, Any] | bytes:
        """Log, forward, record and journal one request to ``full_path``."""
        received_at = timestamp()
        client_address = f"{request.environ.get('REMOTE_ADDR', 'unknown')}:{request.environ.get('REMOTE_PORT', 'unknown')}"
        log_request_received(request, client_address, redaction)
        described = describe_request(request, full_path, redaction)

        if is_reserved_path(full_path):
            log_response_reserved(request.method, full_path)
            reserved = reserved_response()
            journal.add(described, received_at, "rejected", 404)
            log_response_sent()
            return reserved

        pairs = request_header_pairs(request.environ)
        transport = transport_header_names(pairs)
        body = request.body.read()
        request.body.seek(0)
        forwarded = [
            (n, v)
            for n, v in pairs
            if n.lower() not in transport and n.lower() != "accept-encoding"
        ]
        forwarded.append(("Accept-Encoding", "identity"))

        try:
            answer = upstream.exchange(
                request.method, _raw_target(request, full_path), forwarded, body
            )
        except UpstreamError as e:
            cause = str(e)
            log_record_upstream_error(request.method, full_path, cause)
            response.status = 502
            response.set_header(MARKER_HEADER, "upstream-error")
            journal.add(described, received_at, "upstream_error", 502)
            log_response_sent()
            return {"httpmocker": "upstream-error", "cause": cause}

        # A HEAD response keeps its Content-Length: it tells the size of the
        # body a GET would get
        response_transport = transport_header_names(answer.headers)
        if request.method == "HEAD":
            response_transport -= {"content-length"}
        returned = [(n, v) for n, v in answer.headers if n.lower() not in response_transport]

        query: dict[str, list[str]] = {}
        for name, value in parse_query(request.query_string):
            query.setdefault(name, []).append(value)
        interaction = {
            "request": {
                "method": request.method,
                "path": full_path,
                "query": redaction.query_of(query, console_placeholder),
                "headers": redaction.headers_of(
                    header_mapping(pairs, exclude=transport), console_placeholder
                ),
                **represent_body(body, request.environ.get("CONTENT_TYPE")),
            },
            "response": {
                "status": answer.status,
                "headers": redaction.headers_of(
                    header_mapping(answer.headers, exclude=response_transport), console_placeholder
                ),
                **_recorded_response_body(answer),
            },
            "meta": {"recorded_at": timestamp(), "duration_ms": answer.duration_ms, "delay_ms": 0},
        }
        # On disk before the client sees the response
        index = writer.append(interaction)
        journal.add(described, received_at, "recorded", answer.status)
        log_record_recorded(request.method, full_path, answer.status, answer.duration_ms, index)
        sent = send_as_given(request, answer.status, returned, answer.body)
        log_response_sent()
        return sent

    @app.route("/<path:path>", method=list(SUPPORTED_METHODS))
    def handle_request(path: str) -> dict[str, Any] | bytes:
        """Forward a request to the target and record the exchange."""
        return serve(f"/{path}" if path else "/")

    @app.route("/", method=list(SUPPORTED_METHODS))
    def handle_root() -> dict[str, Any] | bytes:
        """Forward a request to the root path and record the exchange."""
        return serve("/")

    return without_default_content_type(app)
