"""Tests for the CLI entry point in __main__.

The blocking server loop is not exercised; these cover argument parsing, port
availability checking, and the --validate-config path of main().
"""

import json
import socket
from wsgiref.simple_server import WSGIRequestHandler

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
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
        try:
            with pytest.raises(SystemExit) as exc_info:
                check_port_available(port)
            assert exc_info.value.code == 1
        finally:
            sock.close()


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
