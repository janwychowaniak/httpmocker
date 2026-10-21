import json
import os
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator

from .journal import RESERVED_PREFIX, is_reserved_path

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# The methods the server routes. Bottle answers any other method with its own
# 405 page, so an endpoint configured with one could never be matched.
SUPPORTED_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")


class Endpoint(BaseModel):
    """Configuration model for a single API endpoint."""

    method: str = Field(..., description="HTTP method (case-sensitive, uppercase)")
    path: str = Field(..., description="Exact path to match")
    status: int = Field(..., description="HTTP status code to return")
    delay_ms: int = Field(..., description="Response delay in milliseconds")
    payload_inline: dict[str, Any] | list[Any] | None = Field(
        None, description="Inline JSON payload (object or array)"
    )
    payload_file: str | None = Field(None, description="Path to external payload file")

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        """Ensure HTTP method is uppercase and one the server routes."""
        if not v.isupper():
            raise ValueError(f"HTTP method must be uppercase, got: {v}")
        if v not in SUPPORTED_METHODS:
            supported = ", ".join(SUPPORTED_METHODS)
            raise ValueError(f"HTTP method must be one of {supported}, got: {v}")
        return v

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        """Ensure path looks like a request path: a leading '/', no query or fragment.

        Matching ignores the query string and clients do not send the fragment, so
        a path with '?' or '#' would only match a request that encodes the
        character as %3F or %23. Paths under /_httpmocker belong to the server
        itself (the journal), so no endpoint can be served there.
        """
        if not v.startswith("/"):
            raise ValueError(f"Path must start with '/', got: {v!r}")
        if "?" in v or "#" in v:
            base = v.split("?", 1)[0].split("#", 1)[0]
            raise ValueError(
                f"Path must not contain '?' or '#', got: {v!r}; query strings and fragments"
                f" are not part of matching, so use {base!r}, which answers any query string"
            )
        if is_reserved_path(v):
            raise ValueError(
                f"Path must not be under {RESERVED_PREFIX!r}, which httpmocker reserves for"
                f" its journal, got: {v!r}"
            )
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: int) -> int:
        """Ensure status code is valid HTTP status."""
        if not 100 <= v <= 599:
            raise ValueError(f"HTTP status code must be between 100-599, got: {v}")
        return v

    @field_validator("delay_ms")
    @classmethod
    def validate_delay(cls, v: int) -> int:
        """Ensure delay is non-negative."""
        if v < 0:
            raise ValueError(f"Delay must be non-negative, got: {v}")
        return v

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        # Ensure exactly one payload type is specified
        has_inline = self.payload_inline is not None
        has_file = self.payload_file is not None

        if not has_inline and not has_file:
            raise ValueError("Either payload_inline or payload_file must be specified")
        if has_inline and has_file:
            raise ValueError("Cannot specify both payload_inline and payload_file")


class Config(BaseModel):
    """Configuration model for the entire application."""

    endpoints: list[Endpoint] = Field(..., description="List of endpoint configurations")

    @field_validator("endpoints")
    @classmethod
    def validate_endpoints_not_empty(cls, v: list[Endpoint]) -> list[Endpoint]:
        """Ensure at least one endpoint is configured."""
        if not v:
            raise ValueError("At least one endpoint must be configured")
        return v


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def load_config(config_path: str) -> Config:
    """
    Load and validate configuration from JSON file.

    Args:
        config_path: Path to the configuration JSON file

    Returns:
        Validated Config object

    Raises:
        SystemExit: On configuration errors (with clean error messages)
    """
    try:
        # Check if config file exists
        if not os.path.exists(config_path):
            print(f"Error: Configuration file not found: {config_path}")
            raise SystemExit(1)

        # Load and parse JSON
        try:
            with open(config_path, encoding="utf-8") as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in configuration file: {e}")
            raise SystemExit(1) from e
        except Exception as e:
            print(f"Error: Could not read configuration file: {e}")
            raise SystemExit(1) from e

        # Validate configuration structure
        try:
            config = Config(**config_data)
        except Exception as e:
            print(f"Error: Invalid configuration: {e}")
            raise SystemExit(1) from e

        # Validate payload files exist
        _validate_payload_files(config)

        return config

    except SystemExit:
        raise
    except Exception as e:
        print(f"Error: Unexpected error loading configuration: {e}")
        raise SystemExit(1) from e


def _validate_payload_files(config: Config) -> None:
    """
    Validate that all referenced payload files exist and hold valid JSON.

    The server reads them again on every request; checking them here stops it
    at startup instead of failing each request to a broken file with a 500.

    Args:
        config: Validated configuration object

    Raises:
        SystemExit: If any payload file is missing, unreadable or not valid JSON
    """
    missing_files: list[str] = []
    invalid_files: list[str] = []

    # A file shared by several endpoints is checked and reported once
    file_paths = [e.payload_file for e in config.endpoints if e.payload_file is not None]
    for file_path in dict.fromkeys(file_paths):
        if not os.path.exists(file_path):
            missing_files.append(file_path)
            continue
        try:
            with open(file_path, encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as e:
            invalid_files.append(f"{file_path}: not valid JSON ({e})")
        except UnicodeDecodeError:
            invalid_files.append(f"{file_path}: not UTF-8 text")
        except OSError as e:
            invalid_files.append(f"{file_path}: cannot be read ({e.strerror})")

    if missing_files:
        print("Error: Missing payload files:")
        for file_path in missing_files:
            print(f"  - {file_path}")
    if invalid_files:
        print("Error: Invalid payload files:")
        for problem in invalid_files:
            print(f"  - {problem}")
    if missing_files or invalid_files:
        raise SystemExit(1)


def load_payload_file(file_path: str) -> dict[str, Any] | list[Any]:
    """
    Load JSON payload from file.

    Args:
        file_path: Path to the payload JSON file

    Returns:
        Parsed JSON data

    Raises:
        SystemExit: On file loading or JSON parsing errors
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            return cast("dict[str, Any] | list[Any]", json.load(f))
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in payload file {file_path}: {e}")
        raise SystemExit(1) from e
    except Exception as e:
        print(f"Error: Could not read payload file {file_path}: {e}")
        raise SystemExit(1) from e
