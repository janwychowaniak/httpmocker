"""Tests for sending a response exactly as recorded or forwarded (serving)."""

from io import BytesIO

from bottle import Bottle, request

from httpmocker.serving import send_as_given, without_default_content_type


def make_app(status, headers, body):
    app = Bottle()

    @app.route("/", method=["GET", "HEAD"])
    def answer():
        return send_as_given(request, status, headers, body)

    @app.route("/plain")
    def plain():
        return "bottle's own"

    return without_default_content_type(app)


def call(app, method="GET", path="/"):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": BytesIO(),
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(app(environ, start_response))
    names = [name.lower() for name, _ in captured["headers"]]
    return captured["status"], captured["headers"], names, body


class TestSendAsGiven:
    def test_status_headers_and_body(self):
        app = make_app(
            201, [("content-type", "application/json"), ("x-rep", "1"), ("x-rep", "2")], b'{"id":7}'
        )
        status, headers, names, body = call(app)
        assert status.startswith("201")
        assert ("Content-Type", "application/json") in headers
        assert [v for n, v in headers if n.lower() == "x-rep"] == ["1", "2"]
        assert ("Content-Length", "8") in headers
        assert body == b'{"id":7}'

    def test_no_content_type_is_added(self):
        status, headers, names, body = call(make_app(200, [("x-trace", "t")], b"raw"))
        assert "content-type" not in names
        assert body == b"raw"

    def test_other_routes_keep_bottles_content_type(self):
        status, headers, names, body = call(make_app(200, [], b"x"), path="/plain")
        assert "content-type" in names

    def test_head_drops_the_body_and_keeps_a_given_content_length(self):
        app = make_app(200, [("content-type", "text/plain"), ("content-length", "17")], b"ignored")
        status, headers, names, body = call(app, "HEAD")
        assert body == b""
        assert ("Content-Length", "17") in headers

    def test_unknown_status_code(self):
        status, *_ = call(make_app(599, [], b""))
        assert status.startswith("599")
