"""Tests for the Bottle application built by request_handler.create_app.

The app is a plain WSGI callable, so it is driven directly through a tiny
self-contained WSGI client. This keeps the test suite dependency-free and
exercises the real HTTP semantics (status codes, Content-Type, 204/HEAD
handling, 404 fallback).
"""

import json
import time
from dataclasses import dataclass
from io import BytesIO

import pytest

from httpmocker.config_loader import Config
from httpmocker.request_handler import _interruptible_delay, create_app


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes

    @property
    def content_type(self):
        return self.headers.get("Content-Type")

    @property
    def json(self):
        return json.loads(self.body.decode("utf-8"))


def request(app, method, path, query="", body=b"", headers=None):
    """Invoke the WSGI app and collect the response."""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(body),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(body)),
    }
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value

    captured = {}

    def start_response(status, response_headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = response_headers

    result = app(environ, start_response)
    try:
        body_bytes = b"".join(result)
    finally:
        if hasattr(result, "close"):
            result.close()

    status_code = int(captured["status"].split(" ", 1)[0])
    headers = dict(captured["headers"])
    return Response(status_code, headers, body_bytes)


def make_app(endpoints):
    """Build a WSGI app configured with the given endpoints."""
    return create_app(Config(endpoints=endpoints))


class TestMatching:
    """Endpoint matching by (method, path)."""

    def test_get_inline_object(self):
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/api/users",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"users": [1, 2]},
                }
            ]
        )
        r = request(app, "GET", "/api/users")
        assert r.status == 200
        assert r.content_type == "application/json"
        assert r.json == {"users": [1, 2]}

    def test_unmatched_path_returns_404(self):
        app = make_app(
            [{"method": "GET", "path": "/a", "status": 200, "delay_ms": 0, "payload_inline": {}}]
        )
        r = request(app, "GET", "/nope")
        assert r.status == 404
        assert r.content_type == "application/json"
        assert r.json == {"error": "endpoint not found"}

    def test_unmatched_method_returns_404(self):
        app = make_app(
            [{"method": "GET", "path": "/a", "status": 200, "delay_ms": 0, "payload_inline": {}}]
        )
        assert request(app, "POST", "/a").status == 404

    def test_trailing_slash_is_significant(self):
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/api/users",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"ok": True},
                }
            ]
        )
        assert request(app, "GET", "/api/users").status == 200
        assert request(app, "GET", "/api/users/").status == 404

    def test_query_params_ignored_for_matching(self):
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/api/users",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"ok": True},
                }
            ]
        )
        r = request(app, "GET", "/api/users", query="filter=active&x=1")
        assert r.status == 200
        assert r.json == {"ok": True}

    def test_root_path_matched(self):
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"root": True},
                }
            ]
        )
        r = request(app, "GET", "/")
        assert r.status == 200
        assert r.json == {"root": True}

    def test_root_path_unmatched(self):
        app = make_app(
            [{"method": "GET", "path": "/x", "status": 200, "delay_ms": 0, "payload_inline": {}}]
        )
        assert request(app, "GET", "/").status == 404


class TestMethodsAndStatus:
    """All configured HTTP methods route, and status codes propagate."""

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    def test_methods_supported(self, method):
        app = make_app(
            [
                {
                    "method": method,
                    "path": "/r",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"m": method},
                }
            ]
        )
        r = request(app, method, "/r")
        assert r.status == 200
        assert r.json == {"m": method}

    def test_status_code_propagates(self):
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/e",
                    "status": 503,
                    "delay_ms": 0,
                    "payload_inline": {"error": "service_unavailable"},
                }
            ]
        )
        r = request(app, "GET", "/e")
        assert r.status == 503
        assert r.json == {"error": "service_unavailable"}


class TestHttpSemantics:
    """204 No Content and HEAD have special body/header handling."""

    def test_204_has_no_content_type_and_empty_body(self):
        app = make_app(
            [{"method": "DELETE", "path": "/s", "status": 204, "delay_ms": 0, "payload_inline": {}}]
        )
        r = request(app, "DELETE", "/s")
        assert r.status == 204
        assert r.body == b""
        assert "Content-Type" not in r.headers

    def test_head_empty_body_with_content_length(self):
        payload = {"a": 1, "b": 2}
        app = make_app(
            [
                {
                    "method": "HEAD",
                    "path": "/h",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": payload,
                }
            ]
        )
        r = request(app, "HEAD", "/h")
        assert r.status == 200
        assert r.body == b""
        assert r.content_type == "application/json"
        expected = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        assert int(r.headers["Content-Length"]) == expected


class TestPayloadFile:
    """payload_file responses support both JSON objects and arrays."""

    def test_payload_file_object(self, tmp_path):
        payload_file = tmp_path / "obj.json"
        payload_file.write_text(json.dumps({"from": "file"}))
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/f",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_file": str(payload_file),
                }
            ]
        )
        assert request(app, "GET", "/f").json == {"from": "file"}

    def test_payload_file_array(self, tmp_path):
        payload_file = tmp_path / "arr.json"
        data = [{"url": "a"}, {"url": "b"}]
        payload_file.write_text(json.dumps(data))
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/f",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_file": str(payload_file),
                }
            ]
        )
        assert request(app, "GET", "/f").json == data


class TestDelay:
    """Configured delays are applied via the interruptible chunked sleep."""

    def test_interruptible_delay_sleeps_for_requested_time(self):
        start = time.monotonic()
        _interruptible_delay(150)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15
        assert elapsed < 1.0

    def test_zero_delay_does_not_sleep(self):
        start = time.monotonic()
        _interruptible_delay(0)
        assert time.monotonic() - start < 0.05

    def test_endpoint_delay_applied_to_response(self):
        app = make_app(
            [
                {
                    "method": "GET",
                    "path": "/d",
                    "status": 200,
                    "delay_ms": 120,
                    "payload_inline": {"ok": True},
                }
            ]
        )
        start = time.monotonic()
        r = request(app, "GET", "/d")
        assert r.status == 200
        assert time.monotonic() - start >= 0.12
