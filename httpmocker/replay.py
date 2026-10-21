import base64
from typing import TYPE_CHECKING, Any

from bottle import BaseRequest, Bottle, request, response

from .cassette import Cassette, CassetteRequest, CassetteResponse
from .config_loader import SUPPORTED_METHODS
from .console_formatter import (
    log_replay_served,
    log_replay_unanswered,
    log_request_received,
    log_response_reserved,
    log_response_sent,
)
from .headers import DEFAULT_REDACTED_HEADERS, HeaderValue, request_header_pairs
from .identity import (
    RequestKey,
    body_key,
    canonical_json,
    headers_key,
    parse_query,
    query_key,
    render_key,
)
from .journal import (
    MARKER_HEADER,
    Journal,
    describe_request,
    install_journal,
    is_reserved_path,
    reserved_response,
    timestamp,
)
from .redaction import Redaction
from .request_handler import _interruptible_delay
from .serving import send_as_given, without_default_content_type

if TYPE_CHECKING:
    from _typeshed.wsgi import WSGIApplication

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def body_bytes(message: CassetteRequest | CassetteResponse) -> bytes:
    """
    The bytes of a recorded body: ``body`` as canonical compact JSON,
    ``body_text`` as UTF-8, ``body_base64`` decoded, nothing without a body.
    """
    if "body" in message.model_fields_set:
        return canonical_json(message.body).encode("utf-8")
    if message.body_text is not None:
        return message.body_text.encode("utf-8")
    if message.body_base64 is not None:
        return base64.b64decode(message.body_base64)
    return b""


def _values_by_name(headers: dict[str, HeaderValue]) -> dict[str, list[str]]:
    """Recorded headers as lists of values by lower-cased name."""
    return {
        name: list(value) if isinstance(value, list) else [value] for name, value in headers.items()
    }


def cassette_key(recorded: CassetteRequest, cassette: Cassette) -> RequestKey:
    """
    The identity key of a recorded request.

    A request recorded with ``body`` keys as JSON whatever its headers say: the
    representation itself says the body is JSON, even in a hand-added
    interaction without a content-type.
    """
    identity = cassette.identity
    values = _values_by_name(recorded.headers)
    pairs = [
        (name, value) for name, query_values in recorded.query.items() for value in query_values
    ]
    content_type = values.get("content-type", [None])[0]
    return RequestKey(
        method=recorded.method,
        path=recorded.path,
        query=query_key(pairs, [*identity.ignore_query_params, *cassette.redacted_query_params]),
        body=body_key(
            body_bytes(recorded),
            content_type,
            identity.ignore_json_paths,
            json_body="body" in recorded.model_fields_set,
        ),
        headers=headers_key(values, identity.match_headers),
    )


def live_key(live: BaseRequest, path: str, cassette: Cassette) -> RequestKey:
    """The identity key of a received request, by the cassette's lists."""
    identity = cassette.identity
    values: dict[str, list[str]] = {}
    for name, value in request_header_pairs(live.environ):
        values.setdefault(name.lower(), []).append(value)
    data = live.body.read()
    live.body.seek(0)
    return RequestKey(
        method=live.method,
        path=path,
        query=query_key(
            parse_query(live.query_string),
            [*identity.ignore_query_params, *cassette.redacted_query_params],
        ),
        body=body_key(data, live.environ.get("CONTENT_TYPE"), identity.ignore_json_paths),
        headers=headers_key(values, identity.match_headers),
    )


class Player:
    """Sequences of interactions per request key and how far each has been served.

    Interactions with equal keys form a sequence in cassette order; the n-th
    request with a key gets the n-th of them.
    """

    def __init__(self, cassette: Cassette) -> None:
        self._sequences: dict[RequestKey, list[int]] = {}
        for index, interaction in enumerate(cassette.interactions):
            self._sequences.setdefault(cassette_key(interaction.request, cassette), []).append(
                index
            )
        self._positions: dict[RequestKey, int] = {}

    def next(self, key: RequestKey) -> tuple[str, int | None, int, int]:
        """
        Take the next interaction for a key.

        Returns:
            (outcome, interaction index, position from 1, sequence length):
            "served" with the index, or "miss" / "exhausted" with None
        """
        sequence = self._sequences.get(key)
        if sequence is None:
            return ("miss", None, 0, 0)
        used = self._positions.get(key, 0)
        if used == len(sequence):
            return ("exhausted", None, used, len(sequence))
        self._positions[key] = used + 1
        return ("served", sequence[used], used + 1, len(sequence))

    def rewind(self) -> None:
        """Start every sequence from its first interaction again."""
        self._positions.clear()


def create_replay_app(cassette: Cassette, journal: Journal | None = None) -> "WSGIApplication":
    """
    Create the WSGI app that serves a cassette (docs/record-replay.md §10).

    Every request except those to the journal gets a journal entry: ``served``
    with the interaction index, ``miss``, ``exhausted``, or ``rejected`` for a
    path under the reserved prefix. Resetting the journal rewinds the sequences.

    Args:
        cassette: Validated cassette
        journal: Journal to record into; a new one when not given

    Returns:
        The WSGI application
    """
    app = Bottle()
    if journal is None:
        journal = Journal()
    player = Player(cassette)
    journal.on_reset(player.rewind)
    install_journal(app, journal)

    # What is shown is redacted with the defaults as well, so a hand edit of the
    # cassette's lists cannot reveal a credential; what is omitted from
    # responses follows the cassette's own list.
    shown = Redaction(
        headers=frozenset(DEFAULT_REDACTED_HEADERS) | frozenset(cassette.redacted_headers),
        query_params=frozenset(cassette.redacted_query_params),
    )
    omitted = frozenset(cassette.redacted_headers)

    def serve(full_path: str) -> dict[str, Any] | bytes:
        """Log, answer and journal one request to ``full_path``."""
        received_at = timestamp()
        client_address = f"{request.environ.get('REMOTE_ADDR', 'unknown')}:{request.environ.get('REMOTE_PORT', 'unknown')}"
        log_request_received(request, client_address, shown)
        described = describe_request(request, full_path, shown)

        if is_reserved_path(full_path):
            log_response_reserved(request.method, full_path)
            reserved = reserved_response()
            journal.add(described, received_at, "rejected", 404)
            log_response_sent()
            return reserved

        key = live_key(request, full_path, cassette)
        outcome, index, position, total = player.next(key)
        if index is None:
            log_replay_unanswered(outcome, request.method, full_path, total)
            response.status = 500
            response.set_header(MARKER_HEADER, outcome)
            rendered = render_key(key)
            journal.add(described, received_at, outcome, 500, key=rendered)
            log_response_sent()
            return {"httpmocker": outcome, "key": rendered}

        recorded = cassette.interactions[index]
        status = recorded.response.status
        delay_ms = recorded.meta.delay_ms
        log_replay_served(request.method, full_path, status, index, position, total, delay_ms)
        if delay_ms > 0:
            _interruptible_delay(delay_ms)
        headers = [
            (name, value)
            for name, values in _values_by_name(recorded.response.headers).items()
            if name not in omitted
            for value in values
        ]
        body = send_as_given(request, status, headers, body_bytes(recorded.response))
        journal.add(described, received_at, "served", status, interaction_index=index)
        log_response_sent()
        return body

    @app.route("/<path:path>", method=list(SUPPORTED_METHODS))
    def handle_request(path: str) -> dict[str, Any] | bytes:
        """Answer a request from the cassette."""
        return serve(f"/{path}" if path else "/")

    @app.route("/", method=list(SUPPORTED_METHODS))
    def handle_root() -> dict[str, Any] | bytes:
        """Answer a request to the root path from the cassette."""
        return serve("/")

    return without_default_content_type(app)
