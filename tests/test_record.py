"""Tests for record mode: the app built by record.create_record_app (docs/record-replay.md §6-§8).

A synthetic stateful upstream runs on stdlib http.server in a thread. The TLS
tests use a CA and a server certificate generated per test session with the
openssl CLI; without openssl they fail when CI is set and are skipped otherwise.
"""

import gzip
import hashlib
import http.client
import json
import os
import shutil
import ssl
import subprocess
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from wsgiref.simple_server import make_server

import pytest

from httpmocker.__main__ import ClientPortRequestHandler
from httpmocker.cassette import load_cassette
from httpmocker.headers import RAW_HEADERS_KEY, RAW_TARGET_KEY
from httpmocker.record import (
    CassetteWriter,
    RecordSettings,
    Upstream,
    _upstream_error,
    check_cassette_path,
    create_record_app,
)
from httpmocker.replay import create_replay_app

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# The synthetic upstream


class UpstreamHandler(BaseHTTPRequestHandler):
    """A tiny stateful API plus endpoints that misbehave on purpose."""

    def log_message(self, *args):
        pass

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def note(self, body):
        self.server.received.append(
            {
                "method": self.command,
                "target": self.path,
                "headers": list(self.headers.items()),
                "body": body,
            }
        )

    def send(self, status, body=b"", headers=(), content_type="application/json"):
        self.send_response(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, value, headers=()):
        self.send(status, json.dumps(value).encode(), headers)

    def do_PUT(self):
        self.note(self.read_body())
        name = self.path.split("?", 1)[0].rsplit("/", 1)[-1]
        items = self.server.items
        if name in items:
            self.send_json(409, {"error": "item already exists", "id": items[name]})
        else:
            items[name] = len(items) + 1
            self.send_json(201, {"id": items[name], "name": name})

    def do_DELETE(self):
        self.note(self.read_body())
        self.server.items.pop(self.path.rsplit("/", 1)[-1], None)
        self.send(204, content_type=None)

    def do_POST(self):
        body = self.read_body()
        self.note(body)
        self.send_json(200, {"received": len(body)})

    def do_HEAD(self):
        self.note(b"")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "42")
        self.end_headers()

    def do_GET(self):
        self.note(b"")
        path = self.path.split("?", 1)[0]
        if path.endswith("/v1/items"):
            self.send_json(200, [{"id": i, "name": n} for n, i in self.server.items.items()])
        elif path == "/slow":
            time.sleep(1.0)
            self.send_json(200, {"slow": True})
        elif path == "/trickle":
            # Never silent for long, but slow in total
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "8")
            self.end_headers()
            for byte in b"trickled":
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.15)
        elif path == "/stream":
            # No Content-Length: the body ends when the connection does
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            for _ in range(10):
                self.wfile.write(b"x")
                self.wfile.flush()
                time.sleep(0.15)
        elif path == "/slow-headers":
            for byte in b"HTTP/1.0 200 OK\r\nContent-Length: 0\r\n\r\n":
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.03)
        elif path == "/hangup":
            self.close_connection = True
            self.wfile.flush()
            self.connection.shutdown(2)
        elif path == "/garbage":
            self.wfile.write(b"NOT HTTP AT ALL\r\n\r\n")
        elif path == "/redirect":
            self.send(302, headers=[("Location", "/elsewhere")], content_type=None)
        elif path == "/bare":
            self.send(200, b"raw", content_type=None)
        elif path == "/gzip":
            self.send(200, gzip.compress(b'{"z": 1}'), [("Content-Encoding", "gzip")])
        elif path == "/cookie":
            self.send_json(200, {"ok": True}, [("Set-Cookie", "sid=abc"), ("Set-Cookie", "t=1")])
        elif path == "/proxy-auth":
            self.send_json(407, {"error": "proxy"}, [("Proxy-Authenticate", "Basic")])
        else:
            self.send_json(200, {"path": self.path})


class SyntheticUpstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, tls_context=None):
        super().__init__(("127.0.0.1", 0), UpstreamHandler)
        if tls_context is not None:
            self.socket = tls_context.wrap_socket(self.socket, server_side=True)
        self.items = {}
        self.received = []
        self.scheme = "https" if tls_context else "http"

    @property
    def url(self):
        host = "localhost" if self.scheme == "https" else "127.0.0.1"
        return f"{self.scheme}://{host}:{self.server_address[1]}"


def run_upstream(tls_context=None):
    server = SyntheticUpstream(tls_context)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def upstream():
    server = run_upstream()
    yield server
    server.shutdown()
    server.server_close()


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# The record app and a WSGI client for it


@dataclass
class Response:
    status: int
    headers: list
    body: bytes

    def header(self, name):
        values = [v for n, v in self.headers if n.lower() == name.lower()]
        return values[0] if len(values) == 1 else (values or None)

    @property
    def json(self):
        return json.loads(self.body.decode("utf-8"))


def call(
    app, method, path, query="", body=b"", content_type=None, raw_headers=None, raw_target=None
):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8080",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(body),
        "wsgi.errors": BytesIO(),
        "CONTENT_LENGTH": str(len(body)),
    }
    if content_type:
        environ["CONTENT_TYPE"] = content_type
    if raw_headers is not None:
        environ[RAW_HEADERS_KEY] = raw_headers
    if raw_target is not None:
        environ[RAW_TARGET_KEY] = raw_target
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    data = b"".join(app(environ, start_response))
    return Response(int(captured["status"].split(" ", 1)[0]), captured["headers"], data)


def recorder(tmp_path, target, upstream_timeout=5.0, **options):
    """A record app writing to tmp_path / "c.json"; returns (app, cassette path)."""
    settings = RecordSettings(
        target=target,
        cassette=str(tmp_path / "c.json"),
        upstream_timeout=upstream_timeout,
        **options,
    )
    writer = CassetteWriter(settings.cassette, settings.cassette_document())
    writer.start()
    return create_record_app(settings, writer), settings.cassette


def cassette_of(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def journal_of(app):
    return call(app, "GET", "/_httpmocker/journal").json


def put(app, name, query="", request_id="r-1", raw_headers=None):
    body = json.dumps({"name": name, "request_id": request_id}).encode()
    return call(
        app,
        "PUT",
        f"/v1/items/{name}",
        query=query,
        body=body,
        content_type="application/json",
        raw_headers=raw_headers
        if raw_headers is not None
        else [("Content-Type", "application/json")],
    )


SCENARIO = [
    ("PUT", "a"),
    ("PUT", "a"),
    ("GET", None),
    ("DELETE", "a"),
    ("DELETE", "a"),
    ("GET", None),
]


def run_scenario(app):
    results = []
    for method, name in SCENARIO:
        if method == "PUT":
            r = put(app, name)
        elif method == "DELETE":
            r = call(app, "DELETE", f"/v1/items/{name}")
        else:
            r = call(app, "GET", "/v1/items")
        results.append((r.status, r.json if r.body else None))
    return results


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


class TestRoundTrip:
    def test_recorded_scenario_replays_identically(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        recorded = run_scenario(app)
        assert [status for status, _ in recorded] == [201, 409, 200, 204, 204, 200]
        assert recorded[1][1] == {"error": "item already exists", "id": 1}

        cassette = cassette_of(path)
        assert [i["request"]["method"] for i in cassette["interactions"]] == [
            m for m, _ in SCENARIO
        ]
        assert journal_of(app)["counts"]["recorded"] == 6

        replay = create_replay_app(load_cassette(path))
        assert run_scenario(replay) == recorded
        counts = journal_of(replay)["counts"]
        assert (counts["served"], counts["miss"], counts["exhausted"]) == (6, 0, 0)

    def test_identity_lists_are_stored_and_used_at_replay(self, tmp_path, upstream):
        app, path = recorder(
            tmp_path, upstream.url, ignore_query_params=("ts",), ignore_json_paths=("/request_id",)
        )
        put(app, "a", query="ts=1", request_id="r-1")
        cassette = cassette_of(path)
        assert cassette["identity"]["ignore_query_params"] == ["ts"]
        assert cassette["identity"]["ignore_json_paths"] == ["/request_id"]
        replay = create_replay_app(load_cassette(path))
        assert put(replay, "a", query="ts=2", request_id="r-2").status == 201

    def test_without_the_lists_changed_values_miss(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        put(app, "a", query="ts=1", request_id="r-1")
        replay = create_replay_app(load_cassette(path))
        assert put(replay, "a", query="ts=2", request_id="r-2").status == 500
        assert journal_of(replay)["counts"]["miss"] == 1


class TestForwarding:
    def test_raw_target_with_the_base_prefix(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url + "/base")
        r = call(app, "GET", "/v1/a/b", query="x=1&x=2", raw_target="/v1/a%2Fb?x=1&x=2")
        assert r.status == 200
        assert upstream.received[0]["target"] == "/base/v1/a%2Fb?x=1&x=2"
        recorded = cassette_of(path)["interactions"][0]["request"]
        assert recorded["path"] == "/v1/a/b"
        assert recorded["query"] == {"x": ["1", "2"]}

    def test_target_is_rebuilt_without_the_raw_one(self, tmp_path, upstream):
        app, _ = recorder(tmp_path, upstream.url)
        call(app, "GET", "/v1/a b", query="q=1")
        assert upstream.received[0]["target"] == "/v1/a%20b?q=1"

    def test_headers_are_forwarded_as_received_except_transport(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        call(
            app,
            "GET",
            "/h",
            raw_headers=[
                ("Host", "mock.test"),
                ("Connection", "keep-alive, X-Hop"),
                ("X-Hop", "1"),
                ("Accept-Encoding", "gzip"),
                ("X-Rep", "1"),
                ("X-Rep", "2"),
                ("x_under", "u"),
            ],
        )
        sent = upstream.received[0]["headers"]
        names = [n.lower() for n, _ in sent]
        assert ("Host", f"127.0.0.1:{upstream.server_address[1]}") in sent
        assert [v for n, v in sent if n == "X-Rep"] == ["1", "2"]
        assert ("x_under", "u") in sent
        assert [v for n, v in sent if n.lower() == "accept-encoding"] == ["identity"]
        assert "x-hop" not in names
        assert "connection" not in names
        recorded = cassette_of(path)["interactions"][0]["request"]["headers"]
        assert recorded == {"accept-encoding": "gzip", "x-rep": ["1", "2"], "x_under": "u"}

    def test_json_request_body_is_recorded_parsed(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        put(app, "a")
        recorded = cassette_of(path)["interactions"][0]
        assert recorded["request"]["body"] == {"name": "a", "request_id": "r-1"}
        assert recorded["response"]["body"] == {"id": 1, "name": "a"}
        assert recorded["meta"]["delay_ms"] == 0
        assert recorded["meta"]["duration_ms"] >= 0

    def test_redirect_is_returned_not_followed(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "GET", "/redirect")
        assert r.status == 302
        assert r.header("Location") == "/elsewhere"
        assert len(upstream.received) == 1
        assert cassette_of(path)["interactions"][0]["response"]["status"] == 302

    def test_response_without_content_type_stays_without(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "GET", "/bare")
        assert r.body == b"raw"
        assert r.header("Content-Type") is None
        recorded = cassette_of(path)["interactions"][0]["response"]
        assert "content-type" not in recorded["headers"]
        assert recorded["body_text"] == "raw"

    def test_encoded_response_is_kept_verbatim(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "GET", "/gzip")
        assert gzip.decompress(r.body) == b'{"z": 1}'
        assert r.header("Content-Encoding") == "gzip"
        recorded = cassette_of(path)["interactions"][0]["response"]
        assert recorded["headers"]["content-encoding"] == "gzip"
        assert "body_base64" in recorded
        replay = create_replay_app(load_cassette(path))
        assert call(replay, "GET", "/gzip").body == r.body

    def test_head_keeps_content_length(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "HEAD", "/v1/items")
        assert (r.status, r.body, r.header("Content-Length")) == (200, b"", "42")
        assert cassette_of(path)["interactions"][0]["response"]["headers"]["content-length"] == "42"
        replay = create_replay_app(load_cassette(path))
        assert call(replay, "HEAD", "/v1/items").header("Content-Length") == "42"

    def test_proxy_authenticate_is_dropped(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "GET", "/proxy-auth")
        assert r.status == 407
        assert r.header("Proxy-Authenticate") is None
        assert (
            "proxy-authenticate" not in cassette_of(path)["interactions"][0]["response"]["headers"]
        )

    def test_root_path(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        assert call(app, "GET", "/").json == {"path": "/"}
        assert cassette_of(path)["interactions"][0]["request"]["path"] == "/"

    def test_reserved_prefix_is_never_forwarded(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "GET", "/_httpmocker/x")
        assert (r.status, r.header("X-Httpmocker")) == (404, "reserved")
        assert upstream.received == []
        assert cassette_of(path)["interactions"] == []
        assert journal_of(app)["counts"]["rejected"] == 1

    def test_console_verdict(self, tmp_path, upstream, capsys):
        app, _ = recorder(tmp_path, upstream.url)
        put(app, "a")
        assert "PUT /v1/items/a → 201 (" in capsys.readouterr().out
        put(app, "a")
        assert "ms) recorded #1" in capsys.readouterr().out

    def test_cassette_validates_after_every_interaction(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        for count, (method, name) in enumerate(SCENARIO, start=1):
            if method == "PUT":
                put(app, name)
            else:
                call(app, method, f"/v1/items/{name}" if name else "/v1/items")
            assert len(load_cassette(path).interactions) == count
        assert sorted(os.listdir(tmp_path)) == ["c.json"]


class TestRedaction:
    def test_redacted_header_and_query_param(self, tmp_path, upstream, capsys):
        app, path = recorder(
            tmp_path, upstream.url, redact_headers=("x-api-key",), redact_query_params=("api_key",)
        )
        call(
            app,
            "GET",
            "/v1/items",
            query="api_key=s3cret&page=2",
            raw_headers=[("X-Api-Key", "k-123"), ("Authorization", "Bearer t0ken")],
        )
        sent = upstream.received[0]
        assert ("X-Api-Key", "k-123") in sent["headers"]
        assert ("Authorization", "Bearer t0ken") in sent["headers"]
        assert "api_key=s3cret" in sent["target"]

        cassette = cassette_of(path)
        recorded = cassette["interactions"][0]["request"]
        assert recorded["headers"]["x-api-key"] == "<redacted>"
        assert recorded["headers"]["authorization"] == "<redacted>"
        assert recorded["query"] == {"api_key": ["<redacted>"], "page": ["2"]}
        assert "x-api-key" in cassette["redacted_headers"]
        assert cassette["redacted_query_params"] == ["api_key"]
        text = json.dumps(cassette)
        assert "k-123" not in text
        assert "s3cret" not in text
        assert "t0ken" not in text

        entry = journal_of(app)["entries"][0]
        digest = hashlib.sha256(b"k-123").hexdigest()[:12]
        assert entry["request"]["headers"]["x-api-key"] == f"<redacted:{digest}>"
        assert entry["request"]["query"]["api_key"][0].startswith("<redacted:")

        out = capsys.readouterr().out
        assert "k-123" not in out
        assert "s3cret" not in out
        assert "api_key=<redacted>&page=2" in out

    def test_set_cookie_reaches_the_client_but_not_the_cassette(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        r = call(app, "GET", "/cookie")
        assert r.header("Set-Cookie") == ["sid=abc", "t=1"]
        recorded = cassette_of(path)["interactions"][0]["response"]["headers"]
        assert recorded["set-cookie"] == ["<redacted>", "<redacted>"]
        replay = create_replay_app(load_cassette(path))
        assert call(replay, "GET", "/cookie").header("Set-Cookie") is None


class TestUpstreamErrors:
    def assert_upstream_error(self, app, path, response, cause):
        assert response.status == 502
        assert response.header("X-Httpmocker") == "upstream-error"
        assert response.json["httpmocker"] == "upstream-error"
        assert cause in response.json["cause"]
        assert journal_of(app)["counts"]["upstream_error"] == 1
        assert cassette_of(path)["interactions"] == []

    def test_connection_refused(self, tmp_path):
        closed = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        port = closed.server_address[1]
        closed.server_close()
        app, path = recorder(tmp_path, f"http://127.0.0.1:{port}")
        self.assert_upstream_error(app, path, call(app, "GET", "/a"), "connection refused")

    def test_unresolvable_host(self, tmp_path):
        app, path = recorder(tmp_path, "http://no-such-host.invalid")
        self.assert_upstream_error(app, path, call(app, "GET", "/a"), "cannot resolve")

    def test_slow_target_exceeds_the_deadline(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url, upstream_timeout=0.3)
        self.assert_upstream_error(app, path, call(app, "GET", "/slow"), "deadline of 0.3 s")

    def test_deadline_bounds_the_whole_exchange_not_each_read(self, tmp_path, upstream):
        """No pause of the trickle reaches 0.5 s, but the whole body takes about 1.2 s."""
        app, path = recorder(tmp_path, upstream.url, upstream_timeout=0.5)
        self.assert_upstream_error(app, path, call(app, "GET", "/trickle"), "deadline of 0.5 s")

    def test_body_ended_by_the_connection_is_not_taken_as_complete(self, tmp_path, upstream):
        """The watchdog cuts the stream; what arrived is not recorded as the whole body."""
        app, path = recorder(tmp_path, upstream.url, upstream_timeout=0.5)
        self.assert_upstream_error(app, path, call(app, "GET", "/stream"), "deadline of 0.5 s")

    def test_slow_status_line_and_headers_count_against_the_deadline(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url, upstream_timeout=0.5)
        self.assert_upstream_error(
            app, path, call(app, "GET", "/slow-headers"), "deadline of 0.5 s"
        )

    def test_stream_within_the_deadline_is_recorded(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url, upstream_timeout=5)
        assert call(app, "GET", "/stream").body == b"x" * 10
        assert cassette_of(path)["interactions"][0]["response"]["body_text"] == "x" * 10

    def test_trickle_within_the_deadline_is_recorded(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url, upstream_timeout=5)
        assert call(app, "GET", "/trickle").body == b"trickled"

    def test_target_closing_without_a_response(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        response = call(app, "GET", "/hangup")
        self.assert_upstream_error(app, path, response, "closed the connection without a response")

    def test_target_answering_garbage(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        response = call(app, "GET", "/garbage")
        self.assert_upstream_error(app, path, response, "invalid response from the target")

    @pytest.mark.parametrize(
        ("error", "cause"),
        [
            (ssl.SSLError(1, "[SSL] wrong version number"), "TLS error"),
            (OSError(101, "Network is unreachable"), "OSError: Network is unreachable"),
        ],
    )
    def test_other_failures_are_described(self, error, cause):
        assert cause in str(_upstream_error(error, "h", "h:1"))

    def test_a_bug_is_not_disguised_as_an_upstream_error(self, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("bug")

        monkeypatch.setattr("httpmocker.record.http.client.HTTPConnection.connect", broken)
        with pytest.raises(RuntimeError, match="bug"):
            Upstream("http://127.0.0.1:9", 1).exchange("GET", "/", [], b"")

    def test_console_line_stands_out(self, tmp_path, upstream, capsys):
        app, _ = recorder(tmp_path, upstream.url, upstream_timeout=0.3)
        call(app, "GET", "/slow")
        assert "UPSTREAM ERROR: GET /slow → 502" in capsys.readouterr().out


class TestChunkedRequestThroughTheServer:
    def test_chunked_body_is_forwarded_with_content_length(self, tmp_path, upstream):
        app, path = recorder(tmp_path, upstream.url)
        server = make_server("127.0.0.1", 0, app, handler_class=ClientPortRequestHandler)
        thread = threading.Thread(target=server.handle_request)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            chunks = iter([b'{"name": ', b'"chunked"}'])
            conn.request(
                "POST",
                "/upload",
                body=chunks,
                headers={"Content-Type": "application/json"},
                encode_chunked=True,
            )
            assert conn.getresponse().status == 200
            conn.close()
        finally:
            thread.join()
            server.server_close()
        sent = upstream.received[0]
        assert sent["body"] == b'{"name": "chunked"}'
        assert ("Content-Length", "19") in sent["headers"]
        assert "transfer-encoding" not in [n.lower() for n, _ in sent["headers"]]
        assert cassette_of(path)["interactions"][0]["request"]["body"] == {"name": "chunked"}


class TestCassettePath:
    def test_absent_and_empty_files_are_accepted(self, tmp_path):
        check_cassette_path(str(tmp_path / "new.json"))
        (tmp_path / "empty.json").write_text("")
        check_cassette_path(str(tmp_path / "empty.json"))

    def test_cassette_without_interactions_is_accepted(self, tmp_path):
        settings = RecordSettings(
            target="http://x", cassette=str(tmp_path / "c.json"), upstream_timeout=1
        )
        CassetteWriter(settings.cassette, settings.cassette_document()).start()
        check_cassette_path(settings.cassette)

    @pytest.mark.parametrize(
        "content",
        [
            "{}",
            "not json",
            json.dumps({"httpmocker_cassette": 1, "interactions": [{}]}),
            json.dumps({"httpmocker_cassette": 2, "interactions": []}),
        ],
    )
    def test_anything_else_is_refused(self, tmp_path, content):
        path = tmp_path / "c.json"
        path.write_text(content)
        with pytest.raises(ValueError, match="already exists and is not empty"):
            check_cassette_path(str(path))

    def test_directory_is_refused(self, tmp_path):
        with pytest.raises(ValueError, match="is a directory"):
            check_cassette_path(str(tmp_path))


class TestCassetteWriter:
    def test_failed_write_leaves_no_temp_file_and_keeps_the_cassette(self, tmp_path, monkeypatch):
        writer = CassetteWriter(str(tmp_path / "c.json"), {"interactions": []})
        writer.start()

        def failing_replace(src, dst):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("httpmocker.record.os.replace", failing_replace)
        with pytest.raises(OSError):
            writer.append({"n": 1})
        assert sorted(os.listdir(tmp_path)) == ["c.json"]
        assert cassette_of(tmp_path / "c.json") == {"interactions": []}

    def test_file_mode_follows_the_umask(self, tmp_path):
        previous = os.umask(0o022)
        try:
            CassetteWriter(str(tmp_path / "c.json"), {"interactions": []}).start()
        finally:
            os.umask(previous)
        assert os.stat(tmp_path / "c.json").st_mode & 0o777 == 0o644


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# TLS


OPENSSL_CONFIG = """
[req]
distinguished_name = dn
prompt = no
[dn]
CN = httpmocker test CA
[ca_ext]
basicConstraints = critical, CA:TRUE
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
[server_ext]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost, IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
"""


@pytest.fixture(scope="session")
def tls_files(tmp_path_factory):
    """A private CA and a server certificate for localhost, valid under VERIFY_X509_STRICT."""
    openssl = shutil.which("openssl")
    if openssl is None:
        if os.environ.get("CI"):
            pytest.fail("openssl is missing: the TLS tests must run in CI")
        pytest.skip("openssl is missing; the TLS tests need it to generate certificates")
    directory = tmp_path_factory.mktemp("tls")
    config = directory / "openssl.cnf"
    config.write_text(OPENSSL_CONFIG)

    def run(*args):
        subprocess.run([openssl, *args], cwd=directory, check=True, capture_output=True)

    run(
        "req", "-x509", "-new", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
        "-keyout", "ca.key", "-out", "ca.pem", "-config", str(config), "-extensions", "ca_ext",
    )  # fmt: skip
    run(
        "req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
        "-keyout", "server.key", "-out", "server.csr", "-config", str(config),
    )  # fmt: skip
    run(
        "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
        "-CAcreateserial", "-days", "3650", "-out", "server.pem",
        "-extfile", str(config), "-extensions", "server_ext",
    )  # fmt: skip
    return directory


@pytest.fixture
def tls_upstream(tls_files):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls_files / "server.pem", tls_files / "server.key")
    server = run_upstream(context)
    yield server
    server.shutdown()
    server.server_close()


class TestTLS:
    def test_untrusted_ca_is_an_upstream_error(self, tmp_path, tls_upstream, monkeypatch):
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        monkeypatch.delenv("SSL_CERT_DIR", raising=False)
        app, path = recorder(tmp_path, tls_upstream.url)
        r = call(app, "GET", "/v1/items")
        assert r.status == 502
        assert "certificate verification failed" in r.json["cause"]
        assert journal_of(app)["counts"]["upstream_error"] == 1
        assert cassette_of(path)["interactions"] == []

    def test_ssl_cert_file_makes_the_ca_trusted(
        self, tmp_path, tls_files, tls_upstream, monkeypatch
    ):
        monkeypatch.setenv("SSL_CERT_FILE", str(tls_files / "ca.pem"))
        app, path = recorder(tmp_path, tls_upstream.url)
        r = call(app, "GET", "/v1/items")
        assert r.status == 200
        assert cassette_of(path)["interactions"][0]["response"]["status"] == 200

    def test_verification_is_always_on(self):
        context = Upstream("https://localhost:1", 1)._context
        assert context is not None
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_certificates_pass_strict_verification(self, tls_files):
        """What VERIFY_X509_STRICT (the default since Python 3.13) checks, on every version."""
        context = ssl.create_default_context(cafile=str(tls_files / "ca.pem"))
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(tls_files / "server.pem", tls_files / "server.key")
        upstream = run_upstream(server)
        try:
            conn = http.client.HTTPSConnection(
                "localhost", upstream.server_address[1], context=context, timeout=5
            )
            conn.request("GET", "/v1/items")
            assert conn.getresponse().status == 200
            conn.close()
        finally:
            upstream.shutdown()
            upstream.server_close()
