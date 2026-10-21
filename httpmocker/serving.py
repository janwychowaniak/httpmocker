from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from bottle import BaseRequest, response

if TYPE_CHECKING:
    from _typeshed.wsgi import StartResponse, WSGIApplication, WSGIEnvironment

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

# Set by send_as_given when the response has no Content-Type of its own
_NO_CONTENT_TYPE_KEY = "httpmocker.no_content_type"


def send_as_given(
    request: BaseRequest, status: int, headers: Iterable[tuple[str, str]], body: bytes
) -> bytes:
    """
    Prepare a response with exactly the given status, headers and body: a
    recorded one in replay, the target's in record mode.

    Content-Length is computed from the body; for a HEAD request the body is
    dropped and a given Content-Length is kept. Bottle adds
    ``Content-Type: text/html`` to a response without one; the app must be
    wrapped in ``without_default_content_type`` so that such a response goes
    out without it.

    Args:
        request: The Bottle request being answered
        status: Status code
        headers: Header names and values, a repeated header once per value
        body: Body bytes

    Returns:
        What the route returns to Bottle
    """
    response.status = status
    has_content_type = False
    for name, value in headers:
        response.add_header(name, value)
        has_content_type = has_content_type or name.lower() == "content-type"
    if not has_content_type:
        request.environ[_NO_CONTENT_TYPE_KEY] = True
    if request.method == "HEAD":
        return b""
    return body


def without_default_content_type(app: "WSGIApplication") -> "WSGIApplication":
    """
    Wrap a Bottle app so that a response prepared by ``send_as_given`` without
    a Content-Type goes out without the one Bottle adds.
    """

    def wrapped(environ: "WSGIEnvironment", start_response: "StartResponse") -> Iterable[bytes]:
        def start(
            status: str, headers: list[tuple[str, str]], exc_info: Any = None
        ) -> Callable[[bytes], object]:
            if environ.get(_NO_CONTENT_TYPE_KEY):
                headers = [
                    (name, value) for name, value in headers if name.lower() != "content-type"
                ]
            return start_response(status, headers, exc_info)

        return app(environ, start)

    return wrapped
