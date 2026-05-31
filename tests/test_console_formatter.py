"""Tests for console_formatter.

Logging functions write to a module-level Rich console; output is captured via
capsys (Rich emits plain text when stdout is not a TTY, e.g. under pytest).
"""

import io
import json

from bottle import BaseRequest

from httpmocker import console_formatter as cf
from httpmocker.config_loader import Endpoint


def make_request(method="GET", path="/", query="", body=b"", content_type=None, headers=None):
    """Build a minimal Bottle request from a WSGI environ."""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
    }
    if content_type:
        environ["CONTENT_TYPE"] = content_type
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    return BaseRequest(environ)


class TestFormatPayloadSource:
    def test_inline_short(self):
        endpoint = Endpoint(
            method="GET", path="/a", status=200, delay_ms=0, payload_inline={"x": 1}
        )
        assert cf.format_payload_source(endpoint) == 'payload_inline: {"x":1}'

    def test_inline_truncated_to_50_chars(self):
        endpoint = Endpoint(
            method="GET",
            path="/a",
            status=200,
            delay_ms=0,
            payload_inline={"key": "v" * 100},
        )
        result = cf.format_payload_source(endpoint)
        assert result.startswith("payload_inline: ")
        value = result[len("payload_inline: ") :]
        assert value.endswith("...")
        assert len(value) == 50

    def test_file(self):
        endpoint = Endpoint(
            method="GET", path="/a", status=200, delay_ms=0, payload_file="payloads/x.json"
        )
        assert cf.format_payload_source(endpoint) == "payload_file:   payloads/x.json"


class TestPrintJsonPayload:
    def test_valid_data_is_printed(self, capsys):
        cf._print_json_payload({"hello": "world"})
        out = capsys.readouterr().out
        assert "hello" in out
        assert "world" in out

    def test_non_serializable_falls_back_to_str(self, capsys):
        sentinel = object()
        cf._print_json_payload(sentinel)
        out = capsys.readouterr().out
        assert "object" in out


class TestLogResponse:
    def test_matched_shows_method_path_status_and_delay(self, capsys):
        cf.log_response_matched("GET", "/a", 200, "payload_inline: {}", 100)
        out = capsys.readouterr().out
        assert "GET /a" in out
        assert "200" in out
        assert "100" in out

    def test_matched_hides_delay_when_zero(self, capsys):
        cf.log_response_matched("GET", "/a", 200, "payload_inline: {}", 0)
        assert "delay_ms" not in capsys.readouterr().out

    def test_not_found(self, capsys):
        cf.log_response_not_found("POST", "/x")
        out = capsys.readouterr().out
        assert "404" in out
        assert "POST /x" in out

    def test_sent(self, capsys):
        cf.log_response_sent()
        assert "sent" in capsys.readouterr().out


class TestLogServerLifecycle:
    def test_startup(self, capsys):
        cf.log_server_startup(8080, "config.json", 3)
        out = capsys.readouterr().out
        assert "starting up" in out
        assert "8080" in out
        assert "config.json" in out
        assert "3" in out

    def test_shutdown(self, capsys):
        cf.log_server_shutdown()
        assert "shutting down" in capsys.readouterr().out


class TestLogRequestReceived:
    def test_basic_get_with_query_and_client(self, capsys):
        request = make_request("GET", "/api/users", query="a=1", headers={"X-Client": "suite"})
        cf.log_request_received(request, "1.2.3.4:5678")
        out = capsys.readouterr().out
        assert "GET /api/users" in out
        assert "?a=1" in out
        assert "1.2.3.4:5678" in out
        assert "suite" in out

    def test_json_body_is_pretty_printed(self, capsys):
        body = json.dumps({"username": "test"}).encode("utf-8")
        request = make_request("POST", "/login", body=body, content_type="application/json")
        cf.log_request_received(request, "x:1")
        out = capsys.readouterr().out
        assert "username" in out
        assert "test" in out

    def test_non_json_body_printed_as_plain_text(self, capsys):
        request = make_request("POST", "/raw", body=b"plain text body", content_type="text/plain")
        cf.log_request_received(request, "x:1")
        assert "plain text body" in capsys.readouterr().out
