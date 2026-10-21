import json
import re
from collections.abc import Iterable
from typing import Any, NamedTuple
from urllib.parse import parse_qsl

from .bodies import is_json_media_type, represent_body

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# A '~' that does not start one of the two escapes RFC 6901 defines.
_INVALID_ESCAPE = re.compile(r"~(?![01])")

# An array index in a JSON Pointer: "0" or a number without leading zeros
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")

# The headers part of a key: per name, absent (None), one value, or the values in order
HeaderKey = tuple[tuple[str, str | tuple[str, ...] | None], ...]


class RequestKey(NamedTuple):
    """The identity of a request (docs/record-replay.md §10); equal keys are the same request."""

    method: str
    path: str
    query: tuple[tuple[str, str], ...]
    # ("json", canonical JSON text) or ("raw", the body bytes)
    body: tuple[str, str | bytes]
    headers: HeaderKey


def parse_json_pointer(pointer: str) -> list[str]:
    """
    Split an RFC 6901 JSON Pointer into its reference tokens.

    The pointer to the whole document ("") is refused: an ignore path deletes a
    part of a request body, never all of it.

    Args:
        pointer: JSON Pointer, e.g. "/items/0/id"

    Returns:
        The unescaped reference tokens, e.g. ["items", "0", "id"]

    Raises:
        ValueError: If the pointer is empty, lacks the leading '/' or holds an
            invalid '~' escape
    """
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/', got: {pointer!r}")
    tokens = pointer[1:].split("/")
    for token in tokens:
        if _INVALID_ESCAPE.search(token):
            raise ValueError(
                f"JSON Pointer has an invalid escape, got: {pointer!r};"
                " write '~' as '~0' and '/' inside a name as '~1'"
            )
    # '~1' first, so that '~01' becomes '~1' and not '/'
    return [token.replace("~1", "/").replace("~0", "~") for token in tokens]


def delete_pointer(document: Any, pointer: str) -> None:
    """
    Delete what a JSON Pointer points at, in place; a pointer that does not
    resolve changes nothing.

    In an array the last token must be an index of an existing element ("-",
    the position past the end, never resolves); deleting an element shifts the
    ones after it.

    Args:
        document: Parsed JSON value, changed in place
        pointer: Valid JSON Pointer (see ``parse_json_pointer``)
    """
    *parents, last = parse_json_pointer(pointer)
    node = document
    for token in parents:
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and _ARRAY_INDEX.fullmatch(token) and int(token) < len(node):
            node = node[int(token)]
        else:
            return
    if isinstance(node, dict):
        node.pop(last, None)
    elif isinstance(node, list) and _ARRAY_INDEX.fullmatch(last) and int(last) < len(node):
        del node[int(last)]


def canonical_json(value: Any) -> str:
    """The canonical form of a JSON value: sorted keys, no spaces, non-ASCII kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def body_key(
    data: bytes,
    content_type: str | None,
    ignore_json_paths: Iterable[str],
    *,
    json_body: bool = False,
) -> tuple[str, str | bytes]:
    """
    The body part of a request key.

    A JSON body (by its media type, or ``json_body``) that parses keys as its
    canonical JSON after the ignore pointers are deleted, in the order given;
    any other body keys as its bytes.

    Args:
        data: Body bytes
        content_type: Content-Type of the request, if any
        ignore_json_paths: JSON Pointers to delete before keying
        json_body: Treat the body as JSON whatever its media type

    Returns:
        ("json", canonical JSON text) or ("raw", the bytes)
    """
    if json_body or is_json_media_type(content_type):
        try:
            value = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError):
            pass
        else:
            for pointer in ignore_json_paths:
                delete_pointer(value, pointer)
            return ("json", canonical_json(value))
    return ("raw", data)


def query_key(
    pairs: Iterable[tuple[str, str]], leave_out: Iterable[str]
) -> tuple[tuple[str, str], ...]:
    """
    The query part of a request key: name and value pairs sorted by name, then
    value, without the names left out.

    Args:
        pairs: Query name and value pairs, decoded
        leave_out: Names that do not take part in identity

    Returns:
        Sorted pairs
    """
    left_out = set(leave_out)
    return tuple(sorted((name, value) for name, value in pairs if name not in left_out))


def parse_query(query_string: str) -> list[tuple[str, str]]:
    """Decode a query string into name and value pairs, blank values kept ("?flag" gives ("flag", ""))."""
    return parse_qsl(query_string, keep_blank_values=True)


def headers_key(values: dict[str, list[str]], match_headers: Iterable[str]) -> HeaderKey:
    """
    The headers part of a request key: per matched name, absent, one value, or
    the values of a repeated header in order.

    Args:
        values: Header values by lower-cased name
        match_headers: Lower-cased names that take part in identity

    Returns:
        One entry per matched name, in the order given
    """
    result: list[tuple[str, str | tuple[str, ...] | None]] = []
    for name in match_headers:
        found = values.get(name, [])
        if not found:
            result.append((name, None))
        elif len(found) == 1:
            result.append((name, found[0]))
        else:
            result.append((name, tuple(found)))
    return tuple(result)


def render_key(key: RequestKey) -> dict[str, Any]:
    """
    Show a request key as JSON (the ``key`` of a miss or exhausted answer).

    Returns:
        ``{"method", "path", "query"}``, plus the body in a representation of
        docs/record-replay.md §9 when there is one, plus ``headers`` when any
        header takes part in matching
    """
    query: dict[str, list[str]] = {}
    for name, value in key.query:
        query.setdefault(name, []).append(value)
    rendered: dict[str, Any] = {"method": key.method, "path": key.path, "query": query}
    kind, body = key.body
    if kind == "json":
        assert isinstance(body, str)
        # As "body", or as "body_text" for a value that would not dump as JSON
        rendered.update(represent_body(body.encode("utf-8"), "application/json"))
    else:
        assert isinstance(body, bytes)
        rendered.update(represent_body(body, None))
    if key.headers:
        rendered["headers"] = {
            name: list(value) if isinstance(value, tuple) else value for name, value in key.headers
        }
    return rendered
