import base64
import json
from typing import Any

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


def is_json_media_type(content_type: str | None) -> bool:
    """
    Tell whether a Content-Type names JSON: ``application/json`` or any
    ``+json`` type, parameters ignored.

    Args:
        content_type: Content-Type header value, if any

    Returns:
        True for a JSON media type
    """
    if not content_type:
        return False
    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def represent_body(data: bytes, content_type: str | None) -> dict[str, Any]:
    """
    Represent a body as stored in cassettes and the journal
    (docs/record-replay.md §9): exactly one of ``body``, ``body_text`` and
    ``body_base64``, or nothing for an empty body.

    Args:
        data: Body bytes
        content_type: Content-Type of the message, if any

    Returns:
        A mapping with at most one of the three keys
    """
    if not data:
        return {}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {"body_base64": base64.b64encode(data).decode("ascii")}
    if is_json_media_type(content_type):
        try:
            value = json.loads(text)
            # A value that only dumps as NaN or Infinity (e.g. 1e400) would be
            # served back as something that is not JSON
            json.dumps(value, allow_nan=False)
        except (ValueError, RecursionError):
            pass
        else:
            return {"body": value}
    return {"body_text": text}
