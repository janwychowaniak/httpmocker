import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote_plus

from .headers import DEFAULT_REDACTED_HEADERS, HeaderValue

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# What a redacted value is replaced with in a cassette and on the console
PLACEHOLDER = "<redacted>"


def journal_placeholder(value: str) -> str:
    """
    Replace a redacted value in the journal.

    The first 12 hex digits of the value's SHA-256 let a test assert which
    credential a request carried; the journal lives in memory only.

    Args:
        value: The original value

    Returns:
        ``<redacted:`` + 12 hex digits + ``>``
    """
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"<redacted:{digest[:12]}>"


def console_placeholder(value: str) -> str:
    """Replace a redacted value on the console (and in a cassette)."""
    return PLACEHOLDER


@dataclass(frozen=True)
class Redaction:
    """The header and query parameter names whose values are hidden.

    Header names are lower-cased; query parameter names are case-sensitive.
    """

    headers: frozenset[str] = frozenset(DEFAULT_REDACTED_HEADERS)
    query_params: frozenset[str] = frozenset()

    def headers_of(
        self, mapping: dict[str, HeaderValue], replace: Callable[[str], str]
    ) -> dict[str, HeaderValue]:
        """
        Redact a header mapping (lower-cased names), each value of a repeated
        header on its own.

        Args:
            mapping: Header mapping as built by ``headers.header_mapping``
            replace: Placeholder function for a redacted value

        Returns:
            A new mapping with redacted values replaced
        """
        redacted: dict[str, HeaderValue] = {}
        for name, value in mapping.items():
            if name not in self.headers:
                redacted[name] = value
            elif isinstance(value, list):
                redacted[name] = [replace(v) for v in value]
            else:
                redacted[name] = replace(value)
        return redacted

    def query_of(
        self, query: dict[str, list[str]], replace: Callable[[str], str]
    ) -> dict[str, list[str]]:
        """
        Redact a parsed query (name to list of values).

        Args:
            query: Parsed query multimap
            replace: Placeholder function for a redacted value

        Returns:
            A new multimap with redacted values replaced
        """
        return {
            name: [replace(v) for v in values] if name in self.query_params else list(values)
            for name, values in query.items()
        }

    def query_string_of(self, query_string: str) -> str:
        """
        Redact a raw query string for the console, leaving every other part of
        it exactly as received.

        Args:
            query_string: Query string without the leading '?'

        Returns:
            The query string with redacted values replaced by the placeholder
        """
        if not self.query_params:
            return query_string
        parts = []
        for part in query_string.split("&"):
            raw_name = part.partition("=")[0]
            if unquote_plus(raw_name) in self.query_params:
                parts.append(f"{raw_name}={PLACEHOLDER}")
            else:
                parts.append(part)
        return "&".join(parts)
