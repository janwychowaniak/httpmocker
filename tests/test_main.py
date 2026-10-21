"""Tests for the CLI entry point in __main__.

The blocking server loop is not exercised; these cover argument parsing (the
three run modes and the record options), port availability checking, the
--validate-config path of main(), shutting the server down on SIGTERM, and the
request handler serving single requests, interrupted or not, with the request
target and headers as received.
"""

import http.client
import json
import signal
import socket
import sys
import threading
import time
from email.message import Message
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from httpmocker.__main__ import (
    DEFAULT_UPSTREAM_TIMEOUT,
    RAW_HEADERS_KEY,
    RAW_TARGET_KEY,
    ClientPortRequestHandler,
    _inject_raw_request,
    _inject_remote_port,
    check_port_available,
    main,
    normalize_target_url,
    parse_arguments,
)


def write_config(tmp_path, endpoints):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"endpoints": endpoints}))
    return config_file


class TestParseArguments:
    def test_parses_required_arguments(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["httpmocker", "-p", "8080", "-c", "config.json"])
        args = parse_arguments()
        assert args.port == 8080
        assert args.config == "config.json"
        assert args.validate_config is False

    def test_validate_config_flag(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["httpmocker", "-p", "9000", "-c", "c.json", "--validate-config"]
        )
        args = parse_arguments()
        assert args.validate_config is True

    def test_missing_required_argument_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["httpmocker", "-p", "8080"])
        with pytest.raises(SystemExit):
            parse_arguments()


class TestCheckPortAvailable:
    def test_free_port_passes(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        sock.close()
        # Should not raise for a free port.
        check_port_available(port)

    def test_port_in_use_exits(self):
        # A server listening on the port, bound the way the server binds.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 0))
        sock.listen()
        port = sock.getsockname()[1]
        try:
            with pytest.raises(SystemExit) as exc_info:
                check_port_available(port)
            assert exc_info.value.code == 1
        finally:
            sock.close()

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows does not block the bind")
    def test_port_left_in_time_wait_passes(self):
        """A stopped server's connections stay in TIME_WAIT for a minute; the
        server binds over them, so a restart must not report the port in use."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", 0))
        server.listen()
        port = server.getsockname()[1]
        # Serve one connection as wsgiref does: the server closes it first, so
        # it is left in TIME_WAIT once the client closes too.
        client = socket.create_connection(("127.0.0.1", port))
        conn, _ = server.accept()
        conn.close()
        client.recv(1)
        client.close()
        server.close()
        # The connection in TIME_WAIT blocks a plain bind to the port...
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as plain, pytest.raises(OSError):
            plain.bind(("0.0.0.0", port))
        # ...but not the check.
        check_port_available(port)


class TestMainValidateConfig:
    def test_valid_config_reports_and_returns(self, tmp_path, monkeypatch, capsys):
        config_file = write_config(
            tmp_path,
            [
                {
                    "method": "GET",
                    "path": "/a",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"ok": True},
                }
            ],
        )
        monkeypatch.setattr(
            "sys.argv", ["httpmocker", "-p", "8080", "-c", str(config_file), "--validate-config"]
        )
        # Returns normally (does not start the server, does not raise).
        main()
        out = capsys.readouterr().out
        assert "is valid" in out
        assert "1 endpoint" in out

    def test_missing_config_exits(self, tmp_path, monkeypatch):
        missing = tmp_path / "nope.json"
        monkeypatch.setattr(
            "sys.argv", ["httpmocker", "-p", "8080", "-c", str(missing), "--validate-config"]
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

    def test_invalid_json_exits(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json")
        monkeypatch.setattr(
            "sys.argv", ["httpmocker", "-p", "8080", "-c", str(bad), "--validate-config"]
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


class TestMainShutdown:
    """SIGTERM (what `docker stop` sends) shuts the server down like Ctrl+C."""

    @pytest.fixture(autouse=True)
    def restore_sigterm_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        yield
        signal.signal(signal.SIGTERM, previous)

    def test_sigterm_shuts_down_cleanly(self, tmp_path, monkeypatch, capsys):
        config_file = write_config(
            tmp_path,
            [
                {
                    "method": "GET",
                    "path": "/a",
                    "status": 200,
                    "delay_ms": 0,
                    "payload_inline": {"ok": True},
                }
            ],
        )
        monkeypatch.setattr("sys.argv", ["httpmocker", "-p", "0", "-c", str(config_file)])

        def serve_until_signalled(*args, **kwargs):
            # Stands in for bottle.run blocking in the server loop. Without a
            # handler SIGTERM would end the test process, so check first.
            if not callable(signal.getsignal(signal.SIGTERM)):
                pytest.fail("no SIGTERM handler is installed when the server starts")
            signal.raise_signal(signal.SIGTERM)
            time.sleep(5)
            pytest.fail("SIGTERM did not stop the server")

        monkeypatch.setattr("httpmocker.__main__.run", serve_until_signalled)
        # Returns normally: a clean shutdown, not an error exit.
        main()
        assert "httpmocker shutting down" in capsys.readouterr().out


def serve_one_request(app, *, interrupted=False):
    """Serve a single GET with ClientPortRequestHandler; return what the client got.

    With ``interrupted``, serving the request must raise KeyboardInterrupt.
    """
    server = make_server("127.0.0.1", 0, app, handler_class=ClientPortRequestHandler)
    received = []

    def client():
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            conn.request("GET", "/a")
            received.append(conn.getresponse().status)
        except http.client.RemoteDisconnected:
            received.append("no response")
        finally:
            conn.close()

    thread = threading.Thread(target=client)
    thread.start()
    try:
        if interrupted:
            with pytest.raises(KeyboardInterrupt):
                server.handle_request()
        else:
            server.handle_request()
    finally:
        thread.join()
        server.server_close()
    return received


class TestInterruptedRequest:
    """Ctrl+C or SIGTERM during a response (e.g. its delay) stops the server.

    wsgiref alone answers the interrupted request with a 500 and keeps serving.
    """

    def test_interrupt_stops_the_server_without_a_response(self):
        def interrupted_app(environ, start_response):
            raise KeyboardInterrupt

        assert serve_one_request(interrupted_app, interrupted=True) == ["no response"]

    def test_other_errors_still_get_a_500(self, capsys):
        def failing_app(environ, start_response):
            raise ValueError("boom")

        assert serve_one_request(failing_app) == [500]

    def test_normal_request_is_served(self):
        def ok_app(environ, start_response):
            start_response("200 OK", [("Content-Type", "application/json")])
            return [b"{}"]

        assert serve_one_request(ok_app) == [200]


class TestClientPortHandler:
    """The custom WSGI handler exposes the client's source port (wsgiref does not)."""

    def test_inject_remote_port_adds_port(self):
        environ = {"REMOTE_ADDR": "203.0.113.7"}
        result = _inject_remote_port(environ, ("203.0.113.7", 49152))
        assert result["REMOTE_PORT"] == "49152"
        assert result["REMOTE_ADDR"] == "203.0.113.7"

    def test_get_environ_includes_client_port(self, monkeypatch):
        monkeypatch.setattr(
            WSGIRequestHandler, "get_environ", lambda self: {"REMOTE_ADDR": "203.0.113.7"}
        )
        handler = object.__new__(ClientPortRequestHandler)
        handler.client_address = ("203.0.113.7", 49152)
        env = handler.get_environ()
        assert env["REMOTE_ADDR"] == "203.0.113.7"
        assert env["REMOTE_PORT"] == "49152"

    def test_address_string_skips_reverse_dns(self):
        handler = object.__new__(ClientPortRequestHandler)
        handler.client_address = ("203.0.113.7", 49152)
        assert handler.address_string() == "203.0.113.7"

    def test_log_request_is_silent(self, capsys):
        handler = object.__new__(ClientPortRequestHandler)
        handler.log_request(200, 123)
        assert capsys.readouterr().out == ""


def parse(monkeypatch, *argv):
    """Parse ``httpmocker -p 8080 <argv>``."""
    monkeypatch.setattr("sys.argv", ["httpmocker", "-p", "8080", *argv])
    return parse_arguments()


def assert_usage_error(monkeypatch, capsys, *argv, message):
    """Parsing ``argv`` exits with argparse's usage status and names the problem."""
    with pytest.raises(SystemExit) as exc_info:
        parse(monkeypatch, *argv)
    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


RECORD = ("--record", "https://api.example.test", "--cassette", "c.json")


class TestParseModes:
    """Exactly one of -c, --record and --replay; the record options only with --record."""

    def test_replay_mode(self, monkeypatch):
        args = parse(monkeypatch, "--replay", "c.json")
        assert args.replay == "c.json"
        assert args.config is None
        assert args.record is None

    def test_replay_with_validate_config(self, monkeypatch):
        args = parse(monkeypatch, "--replay", "c.json", "--validate-config")
        assert args.validate_config is True

    def test_record_mode_defaults(self, monkeypatch):
        args = parse(monkeypatch, *RECORD)
        assert args.record == "https://api.example.test"
        assert args.cassette == "c.json"
        assert args.upstream_timeout == DEFAULT_UPSTREAM_TIMEOUT
        assert args.ignore_query_param == []
        assert args.ignore_json_path == []
        assert args.match_header == []
        assert args.redact_header == []
        assert args.redact_query_param == []

    def test_record_options_are_collected_and_normalized(self, monkeypatch):
        args = parse(
            monkeypatch,
            "--record",
            "http://localhost:9000/v1/",
            "--cassette",
            "c.json",
            "--upstream-timeout",
            "2.5",
            "--ignore-query-param",
            "ts",
            "--ignore-query-param",
            "nonce",
            "--ignore-json-path",
            "/request_id",
            "--match-header",
            "User-Agent",
            "--redact-header",
            "X-Api-Key",
            "--redact-query-param",
            "api_key",
        )
        assert args.record == "http://localhost:9000/v1"
        assert args.upstream_timeout == 2.5
        assert args.ignore_query_param == ["ts", "nonce"]
        assert args.ignore_json_path == ["/request_id"]
        assert args.match_header == ["user-agent"]
        assert args.redact_header == ["x-api-key"]
        assert args.redact_query_param == ["api_key"]

    def test_modes_are_mutually_exclusive(self, monkeypatch, capsys):
        assert_usage_error(
            monkeypatch, capsys, "-c", "c.json", "--replay", "r.json", message="not allowed with"
        )

    def test_record_requires_cassette(self, monkeypatch, capsys):
        assert_usage_error(
            monkeypatch,
            capsys,
            "--record",
            "https://api.example.test",
            message="--record requires --cassette",
        )

    def test_validate_config_does_not_apply_to_record(self, monkeypatch, capsys):
        assert_usage_error(
            monkeypatch, capsys, *RECORD, "--validate-config", message="--validate-config"
        )

    @pytest.mark.parametrize("mode", [("-c", "c.json"), ("--replay", "r.json")])
    @pytest.mark.parametrize(
        "option",
        [
            ("--cassette", "x.json"),
            ("--upstream-timeout", "5"),
            ("--ignore-query-param", "ts"),
            ("--ignore-json-path", "/id"),
            ("--match-header", "user-agent"),
            ("--redact-header", "x-api-key"),
            ("--redact-query-param", "api_key"),
        ],
    )
    def test_record_options_outside_record_are_refused(self, monkeypatch, capsys, mode, option):
        assert_usage_error(
            monkeypatch, capsys, *mode, *option, message=f"{option[0]}: only valid with --record"
        )


class TestRecordOptionChecks:
    @pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
    def test_upstream_timeout_must_be_positive_and_finite(self, monkeypatch, capsys, value):
        assert_usage_error(
            monkeypatch,
            capsys,
            *RECORD,
            "--upstream-timeout",
            value,
            message="--upstream-timeout must be a positive number",
        )

    @pytest.mark.parametrize("pointer", ["", "request_id", "/a~2b"])
    def test_ignore_json_path_must_be_a_json_pointer(self, monkeypatch, capsys, pointer):
        assert_usage_error(
            monkeypatch, capsys, *RECORD, "--ignore-json-path", pointer, message="JSON Pointer"
        )

    @pytest.mark.parametrize(
        "header", ["Authorization", "proxy-authorization", "cookie", "Set-Cookie"]
    )
    def test_default_redacted_header_cannot_be_matched(self, monkeypatch, capsys, header):
        assert_usage_error(
            monkeypatch,
            capsys,
            *RECORD,
            "--match-header",
            header,
            message="a redacted header cannot take part in matching",
        )

    def test_header_redacted_by_flag_cannot_be_matched(self, monkeypatch, capsys):
        assert_usage_error(
            monkeypatch,
            capsys,
            *RECORD,
            "--redact-header",
            "X-Api-Key",
            "--match-header",
            "x-api-key",
            message="--match-header x-api-key: a redacted header",
        )

    @pytest.mark.parametrize("header", ["Host", "content-length", "Transfer-Encoding"])
    def test_transport_header_cannot_be_matched(self, monkeypatch, capsys, header):
        assert_usage_error(
            monkeypatch,
            capsys,
            *RECORD,
            "--match-header",
            header,
            message="a transport header cannot take part in matching",
        )

    def test_invalid_target_is_a_usage_error(self, monkeypatch, capsys):
        assert_usage_error(
            monkeypatch,
            capsys,
            "--record",
            "ftp://api.example.test",
            "--cassette",
            "c.json",
            message="http:// or https://",
        )


class TestNormalizeTargetUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://api.example.test", "https://api.example.test"),
            ("http://localhost:8080/", "http://localhost:8080"),
            ("https://api.example.test/v1/prefix/", "https://api.example.test/v1/prefix"),
            ("http://[::1]:9000", "http://[::1]:9000"),
            ("HTTPS://api.example.test", "HTTPS://api.example.test"),
        ],
    )
    def test_accepted_targets(self, url, expected):
        assert normalize_target_url(url) == expected

    @pytest.mark.parametrize(
        ("url", "message"),
        [
            ("ftp://api.example.test", "http:// or https://"),
            ("api.example.test:8080", "http:// or https://"),
            ("http://", "no host"),
            ("http://api.example.test:99999", "invalid port"),
            ("http://api.example.test:http", "invalid port"),
            ("http://api.example.test/?q=1", "query or a fragment"),
            ("http://api.example.test/?", "query or a fragment"),
            ("http://api.example.test/#top", "query or a fragment"),
        ],
    )
    def test_refused_targets(self, url, message):
        with pytest.raises(ValueError, match=message):
            normalize_target_url(url)

    @pytest.mark.parametrize(
        "url", ["https://user:s3cret@api.example.test", "https://user@api.example.test"]
    )
    def test_user_info_is_refused_without_echoing_it(self, url):
        with pytest.raises(ValueError, match="user info") as exc_info:
            normalize_target_url(url)
        assert "s3cret" not in str(exc_info.value)
        assert "user@" not in str(exc_info.value)


class TestMainValidateCassette:
    def test_valid_cassette_reports_and_returns(self, monkeypatch, capsys):
        example = str(Path(__file__).resolve().parent.parent / "cassettes" / "example.json")
        monkeypatch.setattr(
            "sys.argv", ["httpmocker", "-p", "8080", "--replay", example, "--validate-config"]
        )
        main()
        out = capsys.readouterr().out
        assert "is valid" in out
        assert "7 interaction(s)" in out

    def test_invalid_cassette_exits_1(self, tmp_path, monkeypatch, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"httpmocker_cassette": 1, "interactions": []}))
        monkeypatch.setattr(
            "sys.argv", ["httpmocker", "-p", "8080", "--replay", str(bad), "--validate-config"]
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert "At least one interaction" in capsys.readouterr().out


class TestMainReplay:
    """--replay serves a cassette until stopped."""

    @pytest.fixture(autouse=True)
    def restore_sigterm_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        yield
        signal.signal(signal.SIGTERM, previous)

    def test_replay_starts_the_server_with_the_replay_app(self, monkeypatch, capsys):
        example = str(Path(__file__).resolve().parent.parent / "cassettes" / "example.json")
        monkeypatch.setattr("sys.argv", ["httpmocker", "-p", "0", "--replay", example])
        served = {}

        def fake_run(app, **kwargs):
            served["app"] = app
            served["kwargs"] = kwargs
            raise KeyboardInterrupt

        monkeypatch.setattr("httpmocker.__main__.run", fake_run)
        main()
        assert callable(served["app"])
        assert served["kwargs"]["handler_class"] is ClientPortRequestHandler
        out = capsys.readouterr().out
        assert "starting up (replay)" in out
        assert "Interactions:           7" in out
        assert "shutting down" in out

    def test_invalid_cassette_does_not_start(self, tmp_path, monkeypatch, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{")
        monkeypatch.setattr("sys.argv", ["httpmocker", "-p", "0", "--replay", str(bad)])
        monkeypatch.setattr(
            "httpmocker.__main__.run", lambda *a, **k: pytest.fail("server started")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


class TestMainRecord:
    """--record checks the cassette path, writes the empty cassette, then serves."""

    @pytest.fixture(autouse=True)
    def restore_sigterm_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        yield
        signal.signal(signal.SIGTERM, previous)

    def argv(self, cassette):
        return [
            "httpmocker",
            "-p",
            "0",
            "--record",
            "http://127.0.0.1:9",
            "--cassette",
            str(cassette),
        ]

    def test_record_writes_the_empty_cassette_and_serves(self, tmp_path, monkeypatch, capsys):
        cassette = tmp_path / "c.json"
        monkeypatch.setattr("sys.argv", self.argv(cassette))
        served = {}

        def fake_run(app, **kwargs):
            served["app"] = app
            # The cassette is on disk before the server listens
            served["cassette"] = json.loads(cassette.read_text())
            raise KeyboardInterrupt

        monkeypatch.setattr("httpmocker.__main__.run", fake_run)
        main()
        assert callable(served["app"])
        assert served["cassette"]["interactions"] == []
        assert served["cassette"]["target"] == "http://127.0.0.1:9"
        out = capsys.readouterr().out
        assert "starting up (record)" in out
        assert "Upstream deadline:      30 s" in out

    def test_non_empty_cassette_is_refused(self, tmp_path, monkeypatch, capsys):
        cassette = tmp_path / "c.json"
        cassette.write_text("{}")
        monkeypatch.setattr("sys.argv", self.argv(cassette))
        monkeypatch.setattr(
            "httpmocker.__main__.run", lambda *a, **k: pytest.fail("server started")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert "already exists and is not empty" in capsys.readouterr().out
        assert cassette.read_text() == "{}"

    def test_unwritable_cassette_is_refused(self, tmp_path, monkeypatch, capsys):
        cassette = tmp_path / "missing-dir" / "c.json"
        monkeypatch.setattr("sys.argv", self.argv(cassette))
        monkeypatch.setattr(
            "httpmocker.__main__.run", lambda *a, **k: pytest.fail("server started")
        )
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        assert "Cannot write cassette file" in capsys.readouterr().out


def capture_environ(send):
    """Serve one request, written by ``send(conn)``, with ClientPortRequestHandler.

    Returns the environ the WSGI app received.
    """
    captured = {}

    def app(environ, start_response):
        captured.update(environ)
        start_response("204 No Content", [])
        return []

    server = make_server("127.0.0.1", 0, app, handler_class=ClientPortRequestHandler)

    def client():
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            send(conn)
            conn.getresponse().read()
        finally:
            conn.close()

    thread = threading.Thread(target=client)
    thread.start()
    try:
        server.handle_request()
    finally:
        thread.join()
        server.server_close()
    return captured


class TestRawRequest:
    """The handler keeps the request target and headers as the client sent them."""

    def test_inject_raw_request_keeps_order_and_repeats(self):
        headers = Message()
        headers["X-Rep"] = "1"
        headers["Accept"] = "application/json"
        headers["X-Rep"] = " 2 "
        environ = _inject_raw_request({}, "/a%2Fb?x=1", headers)
        assert environ[RAW_TARGET_KEY] == "/a%2Fb?x=1"
        assert environ[RAW_HEADERS_KEY] == [
            ("X-Rep", "1"),
            ("Accept", "application/json"),
            ("X-Rep", "2"),
        ]

    def test_served_request_carries_target_and_headers_as_received(self):
        def send(conn):
            conn.putrequest("GET", "/v1/a%2Fb?x=1&x=2", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", "mock.test")
            conn.putheader("X-Rep", "1")
            conn.putheader("X-Rep", "2")
            conn.putheader("X-Api-Key", "dash")
            conn.putheader("X_Api_Key", "underscore")
            conn.endheaders()

        environ = capture_environ(send)
        assert environ[RAW_TARGET_KEY] == "/v1/a%2Fb?x=1&x=2"
        assert environ[RAW_HEADERS_KEY] == [
            ("Host", "mock.test"),
            ("X-Rep", "1"),
            ("X-Rep", "2"),
            ("X-Api-Key", "dash"),
            ("X_Api_Key", "underscore"),
        ]
        # What wsgiref alone makes of the same request
        assert environ["PATH_INFO"] == "/v1/a/b"
        assert environ["HTTP_X_REP"] == "1,2"
        assert environ["HTTP_X_API_KEY"] == "dash,underscore"
