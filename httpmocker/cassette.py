import base64
import binascii
import json
import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config_loader import check_delay, check_method, check_path, check_status
from .headers import DEFAULT_REDACTED_HEADERS, TRANSPORT_HEADERS, HeaderValue
from .identity import parse_json_pointer

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# The cassette format this version reads and writes (docs/record-replay.md §9)
CASSETTE_FORMAT = 1

BODY_FIELDS = ("body", "body_text", "body_base64")

# Characters no header name or value can carry
_FORBIDDEN_IN_HEADERS = re.compile(r"[\r\n\0]")


def _check_header_names(names: list[str], what: str) -> list[str]:
    """Ensure header names are lower-cased, as cassettes store them."""
    for name in names:
        if name != name.lower():
            raise ValueError(f"{what} must be lower-case, got: {name!r}")
    return names


class _Strict(BaseModel):
    """A cassette part: an unknown field is an error, so a misspelt one is never ignored."""

    model_config = ConfigDict(extra="forbid")


class _Message(_Strict):
    """Headers and body shared by a recorded request and its response."""

    headers: dict[str, HeaderValue] = Field(default_factory=dict)
    body: Any = None
    body_text: str | None = None
    body_base64: str | None = None

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, v: dict[str, HeaderValue]) -> dict[str, HeaderValue]:
        """Ensure lower-cased names, no empty list for a repeated header, and no
        CR, LF or NUL, which no header can carry on the wire."""
        _check_header_names(list(v), "Header names")
        for name, value in v.items():
            if isinstance(value, list) and not value:
                raise ValueError(f"Header {name!r} has an empty list of values")
            for text in [name, *(value if isinstance(value, list) else [value])]:
                if _FORBIDDEN_IN_HEADERS.search(text):
                    raise ValueError(f"Header {name!r} holds a CR, LF or NUL character")
        return v

    @field_validator("body_base64")
    @classmethod
    def validate_body_base64(cls, v: str | None) -> str | None:
        """Ensure body_base64 decodes."""
        if v is not None:
            try:
                base64.b64decode(v, validate=True)
            except binascii.Error as e:
                raise ValueError(f"body_base64 is not valid base64: {e}") from None
        return v

    @model_validator(mode="after")
    def validate_one_body(self) -> "_Message":
        """Ensure at most one body representation; a JSON null in ``body`` counts as one."""
        given = [name for name in BODY_FIELDS if name in self.model_fields_set]
        if len(given) > 1:
            raise ValueError(
                f"Only one of body, body_text and body_base64 may be given, got: {', '.join(given)}"
            )
        return self


class CassetteRequest(_Message):
    """The request of a recorded interaction."""

    method: str
    path: str
    query: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        """Ensure HTTP method is uppercase and one the server routes."""
        return check_method(v)

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        """Ensure path looks like a request path, outside the reserved prefix."""
        return check_path(v)


class CassetteResponse(_Message):
    """The response of a recorded interaction."""

    status: int

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        """Ensure status code is valid HTTP status."""
        return check_status(v)


class Meta(_Strict):
    """When an interaction was recorded, what the target took, and the delay to replay it with."""

    recorded_at: str | None = None
    duration_ms: int | None = None
    delay_ms: int = 0

    @field_validator("duration_ms")
    @classmethod
    def validate_duration(cls, v: int | None) -> int | None:
        """Ensure duration is non-negative."""
        if v is not None and v < 0:
            raise ValueError(f"Duration must be non-negative, got: {v}")
        return v

    @field_validator("delay_ms")
    @classmethod
    def validate_delay(cls, v: int) -> int:
        """Ensure delay is non-negative."""
        return check_delay(v)


class Interaction(_Strict):
    """One recorded request and the response the target gave."""

    request: CassetteRequest
    response: CassetteResponse
    meta: Meta = Field(default_factory=Meta)

    @model_validator(mode="after")
    def validate_no_transport_headers(self) -> "Interaction":
        """Ensure no transport header, except content-length on a HEAD response."""
        request_transport = sorted(TRANSPORT_HEADERS & self.request.headers.keys())
        if request_transport:
            raise ValueError(
                f"Request has transport headers, which are never recorded: {', '.join(request_transport)}"
            )
        allowed = {"content-length"} if self.request.method == "HEAD" else set()
        response_transport = sorted((TRANSPORT_HEADERS - allowed) & self.response.headers.keys())
        if response_transport:
            raise ValueError(
                f"Response has transport headers, which are never recorded: {', '.join(response_transport)}"
                " (only a HEAD response keeps content-length)"
            )
        return self


class Identity(_Strict):
    """What request identity leaves out or adds (docs/record-replay.md §10)."""

    ignore_query_params: list[str] = Field(default_factory=list)
    ignore_json_paths: list[str] = Field(default_factory=list)
    match_headers: list[str] = Field(default_factory=list)

    @field_validator("ignore_json_paths")
    @classmethod
    def validate_pointers(cls, v: list[str]) -> list[str]:
        """Ensure every ignore path is an RFC 6901 JSON Pointer."""
        for pointer in v:
            parse_json_pointer(pointer)
        return v

    @field_validator("match_headers")
    @classmethod
    def validate_match_headers(cls, v: list[str]) -> list[str]:
        """Ensure lower-cased names, none of them a transport header."""
        _check_header_names(v, "match_headers names")
        transport = sorted(TRANSPORT_HEADERS & set(v))
        if transport:
            raise ValueError(
                f"match_headers cannot hold transport headers, which are never recorded: {', '.join(transport)}"
            )
        return v


class Cassette(_Strict):
    """A recorded conversation with a target, as replay serves it."""

    httpmocker_cassette: int
    tool_version: str | None = None
    recorded_at: str | None = None
    target: str | None = None
    identity: Identity = Field(default_factory=Identity)
    redacted_headers: list[str] = Field(default_factory=lambda: list(DEFAULT_REDACTED_HEADERS))
    redacted_query_params: list[str] = Field(default_factory=list)
    interactions: list[Interaction]

    @field_validator("httpmocker_cassette")
    @classmethod
    def validate_format(cls, v: int) -> int:
        """Ensure the cassette is in the format this version reads."""
        if v != CASSETTE_FORMAT:
            raise ValueError(
                f"httpmocker_cassette must be {CASSETTE_FORMAT}, the cassette format this"
                f" version of httpmocker reads, got: {v}"
            )
        return v

    @field_validator("redacted_headers")
    @classmethod
    def validate_redacted_headers(cls, v: list[str]) -> list[str]:
        """Ensure lower-cased names."""
        return _check_header_names(v, "redacted_headers names")

    @field_validator("interactions")
    @classmethod
    def validate_interactions_not_empty(cls, v: list[Interaction]) -> list[Interaction]:
        """Ensure at least one interaction is recorded."""
        if not v:
            raise ValueError("At least one interaction must be recorded")
        return v

    @model_validator(mode="after")
    def validate_matching_not_redacted(self) -> "Cassette":
        """Ensure no redacted header takes part in matching: its value is not kept."""
        both = sorted(set(self.identity.match_headers) & set(self.redacted_headers))
        if both:
            raise ValueError(
                f"match_headers cannot hold redacted headers, whose values are not kept: {', '.join(both)}"
            )
        return self


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def load_cassette(cassette_path: str) -> Cassette:
    """
    Load and validate a cassette from a JSON file.

    Args:
        cassette_path: Path to the cassette file

    Returns:
        Validated Cassette object

    Raises:
        SystemExit: On any problem with the file (with a clean error message)
    """
    if not os.path.exists(cassette_path):
        print(f"Error: Cassette file not found: {cassette_path}")
        raise SystemExit(1)

    try:
        with open(cassette_path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in cassette file: {e}")
        raise SystemExit(1) from e
    except (OSError, UnicodeDecodeError) as e:
        print(f"Error: Could not read cassette file: {e}")
        raise SystemExit(1) from e

    if not isinstance(data, dict):
        print("Error: Invalid cassette: the file must hold a JSON object")
        raise SystemExit(1)

    try:
        return Cassette(**data)
    except ValueError as e:
        print(f"Error: Invalid cassette: {e}")
        raise SystemExit(1) from e
