from collections.abc import Iterable, Mapping
from typing import Any

# Server-specific environ keys (PEP 3333) holding the request as received;
# httpmocker's request handler sets them (see __main__.ClientPortRequestHandler)
RAW_TARGET_KEY = "httpmocker.raw_target"
RAW_HEADERS_KEY = "httpmocker.raw_headers"

# Hop-by-hop and framing headers (docs/record-replay.md §5): never forwarded as
# received, never written to a cassette, never shown in the journal. A message's
# Connection header can name further ones. wsgiref answers 500 when an
# application sets Proxy-Authenticate, so it is on the list too.
TRANSPORT_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
        "proxy-authenticate",
    }
)

# Always redacted (docs/record-replay.md §11); --redact-header adds to them.
DEFAULT_REDACTED_HEADERS = ("authorization", "proxy-authorization", "cookie", "set-cookie")

# A header value as stored: one value, or the values of a repeated header in order
HeaderValue = str | list[str]

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def request_header_pairs(environ: Mapping[str, Any]) -> list[tuple[str, str]]:
    """
    Return the request headers as ``(name, value)`` pairs, in order.

    The pairs come from httpmocker's request handler when it served the
    request. Under another WSGI server they are rebuilt from the ``HTTP_*``
    keys, where repeated headers are already comma-joined and the original
    spelling of the names is lost.

    Args:
        environ: WSGI environ of the request

    Returns:
        Header name and value pairs
    """
    raw = environ.get(RAW_HEADERS_KEY)
    if raw is not None:
        return list(raw)
    pairs = []
    for key, value in environ.items():
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value:
            pairs.append((key.replace("_", "-").title(), value))
        elif key.startswith("HTTP_"):
            pairs.append((key[5:].replace("_", "-").title(), value))
    return pairs


def transport_header_names(pairs: Iterable[tuple[str, str]]) -> frozenset[str]:
    """
    Return the transport headers of a message: the fixed set plus those its
    Connection header names.

    Args:
        pairs: Header name and value pairs of the message

    Returns:
        Lower-cased header names
    """
    named = {
        token.strip().lower()
        for name, value in pairs
        if name.lower() == "connection"
        for token in value.split(",")
        if token.strip()
    }
    return TRANSPORT_HEADERS | named


def header_mapping(
    pairs: Iterable[tuple[str, str]], exclude: frozenset[str] = frozenset()
) -> dict[str, HeaderValue]:
    """
    Collect header pairs into the stored form: lower-cased names, a repeated
    header as the list of its values in order.

    Args:
        pairs: Header name and value pairs
        exclude: Lower-cased names to leave out

    Returns:
        Mapping of lower-cased name to a value or a list of values
    """
    mapping: dict[str, HeaderValue] = {}
    for name, value in pairs:
        key = name.lower()
        if key in exclude:
            continue
        present = mapping.get(key)
        if present is None:
            mapping[key] = value
        elif isinstance(present, list):
            present.append(value)
        else:
            mapping[key] = [present, value]
    return mapping
