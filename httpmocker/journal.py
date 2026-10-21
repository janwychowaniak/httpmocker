from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl

from bottle import BaseRequest, Bottle, response

from .bodies import represent_body
from .headers import header_mapping, request_header_pairs, transport_header_names
from .redaction import Redaction, journal_placeholder

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# Paths the mocked API cannot use (docs/record-replay.md §13)
RESERVED_PREFIX = "/_httpmocker"
JOURNAL_PATH = RESERVED_PREFIX + "/journal"

# Header on every response the tool generates itself (docs/record-replay.md §14)
MARKER_HEADER = "X-Httpmocker"

# Every outcome of an exchange, in the order the counts list them
OUTCOMES = ("served", "recorded", "miss", "exhausted", "upstream_error", "rejected", "unmatched")

MAX_ENTRIES = 10_000
MAX_BODY_BYTES = 64 * 1024

JOURNAL_METHODS = ("GET", "DELETE")
OTHER_METHODS = ("POST", "PUT", "PATCH", "HEAD", "OPTIONS")


def is_reserved_path(path: str) -> bool:
    """Tell whether a path is ``/_httpmocker`` or lies under ``/_httpmocker/``."""
    return path == RESERVED_PREFIX or path.startswith(RESERVED_PREFIX + "/")


def describe_request(request: BaseRequest, path: str, redaction: Redaction) -> dict[str, Any]:
    """
    Describe a request as a journal entry shows it (docs/record-replay.md §12).

    Transport headers are left out and redacted values hashed. A body over
    ``MAX_BODY_BYTES`` is not stored; ``body_size`` is always given.

    Args:
        request: Bottle request
        path: Request path as matched (percent-decoded)
        redaction: Names whose values are redacted

    Returns:
        The ``request`` part of a journal entry
    """
    pairs = request_header_pairs(request.environ)
    headers = header_mapping(pairs, exclude=transport_header_names(pairs))
    query: dict[str, list[str]] = {}
    for name, value in parse_qsl(request.query_string, keep_blank_values=True):
        query.setdefault(name, []).append(value)

    body = request.body.read()
    request.body.seek(0)

    described: dict[str, Any] = {
        "method": request.method,
        "path": path,
        "query": redaction.query_of(query, journal_placeholder),
        "headers": redaction.headers_of(headers, journal_placeholder),
    }
    if len(body) > MAX_BODY_BYTES:
        described["body_omitted"] = True
    else:
        described.update(represent_body(body, request.environ.get("CONTENT_TYPE")))
    described["body_size"] = len(body)
    return described


class Journal:
    """In-memory record of the exchanges the server handled.

    Only the last ``max_entries`` entries are kept; the counts are running
    totals since the last reset, and ``dropped`` counts the evicted entries.
    """

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._counts = dict.fromkeys(OUTCOMES, 0)
        self._dropped = 0
        self._seq = 0
        self._reset_hooks: list[Callable[[], None]] = []

    def on_reset(self, hook: Callable[[], None]) -> None:
        """Call ``hook`` on every reset, e.g. to rewind replay's sequence positions."""
        self._reset_hooks.append(hook)

    def add(
        self,
        request: dict[str, Any],
        received_at: str,
        outcome: str,
        status: int,
        interaction_index: int | None = None,
        key: dict[str, Any] | None = None,
    ) -> None:
        """
        Add an entry for one exchange.

        Args:
            request: Request description from ``describe_request``
            received_at: When the request arrived, from ``timestamp``
            outcome: One of ``OUTCOMES``
            status: Status code answered
            interaction_index: Cassette index of the interaction served (replay)
            key: Request key of a miss or an exhausted sequence (replay)
        """
        if outcome not in self._counts:
            raise ValueError(f"Unknown journal outcome: {outcome}")
        if len(self._entries) == self._entries.maxlen:
            self._dropped += 1
        self._seq += 1
        entry: dict[str, Any] = {
            "seq": self._seq,
            "received_at": received_at,
            "request": request,
            "outcome": outcome,
            "status": status,
        }
        if interaction_index is not None:
            entry["interaction_index"] = interaction_index
        if key is not None:
            entry["key"] = key
        self._entries.append(entry)
        self._counts[outcome] += 1

    def snapshot(self) -> dict[str, Any]:
        """Return the journal as ``GET /_httpmocker/journal`` answers it."""
        return {
            "counts": dict(self._counts),
            "dropped": self._dropped,
            "entries": list(self._entries),
        }

    def reset(self) -> None:
        """Clear the entries, zero the counts, ``dropped`` and ``seq``, and run the reset hooks."""
        self._entries.clear()
        self._counts = dict.fromkeys(OUTCOMES, 0)
        self._dropped = 0
        self._seq = 0
        for hook in self._reset_hooks:
            hook()


def timestamp() -> str:
    """Current UTC time as ISO 8601 with milliseconds, e.g. ``2026-10-21T12:00:00.123Z``."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def install_journal(app: Bottle, journal: Journal) -> None:
    """
    Add the journal endpoint to an app. Requests to it are not journaled.

    ``GET`` returns the journal, ``DELETE`` resets it (``204``), any other of
    the routed methods gets ``405``. Every answer carries the marker header.
    """

    @app.route(JOURNAL_PATH, method="GET")
    def get_journal() -> dict[str, Any]:
        response.set_header(MARKER_HEADER, "journal")
        return journal.snapshot()

    @app.route(JOURNAL_PATH, method="DELETE")
    def reset_journal() -> str:
        journal.reset()
        response.status = 204
        response.set_header(MARKER_HEADER, "journal")
        return ""

    @app.route(JOURNAL_PATH, method=list(OTHER_METHODS))
    def journal_method_not_allowed() -> dict[str, str]:
        response.status = 405
        response.set_header(MARKER_HEADER, "journal")
        response.set_header("Allow", ", ".join(JOURNAL_METHODS))
        return {"httpmocker": "journal", "error": "method not allowed"}


def reserved_response() -> dict[str, str]:
    """Answer a request under the reserved prefix that is not the journal: 404 with the marker."""
    response.status = 404
    response.set_header(MARKER_HEADER, "reserved")
    return {"httpmocker": "reserved"}
