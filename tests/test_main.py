"""Tests for the CLI entry point in __main__.

The blocking server loop is not exercised; these cover argument parsing, port
availability checking, the --validate-config path of main(), shutting the
server down on SIGTERM, and the request handler serving single requests,
interrupted or not.
"""

import http.client
import json
import signal
import socket
import sys
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from httpmocker.__main__ import (
    ClientPortRequestHandler,
    _inject_remote_port,
    check_port_available,
    main,
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
