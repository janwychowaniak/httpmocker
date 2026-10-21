import re

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# A '~' that does not start one of the two escapes RFC 6901 defines.
_INVALID_ESCAPE = re.compile(r"~(?![01])")


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
