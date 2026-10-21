"""Tests for replay: the app built by replay.create_replay_app (docs/record-replay.md §10).

The app is driven directly through a small WSGI client, as in
test_request_handler; ``raw_headers`` stands in for what httpmocker's request
handler passes on as received.
"""

import hashlib
import json
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest

from httpmocker.cassette import Cassette
from httpmocker.headers import RAW_HEADERS_KEY
from httpmocker.journal import Journal
from httpmocker.replay import Player, create_replay_app

EXAMPLE = Path(__file__).resolve().parent.parent / "cassettes" / "example.json"


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


def call(app, method, path, query="", body=b"", content_type=None, raw_headers=None):
    """Invoke the WSGI app and collect the response."""
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
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = headers

    data = b"".join(app(environ, start_response))
    return Response(int(captured["status"].split(" ", 1)[0]), captured["headers"], data)


def journal_of(app):
    r = call(app, "GET", "/_httpmocker/journal")
    assert r.status == 200
    return r.json


def example_app():
    with open(EXAMPLE, encoding="utf-8") as f:
        return create_replay_app(Cassette(**json.load(f)))


def put_item(app, ts="1761050000", request_id="r-9"):
    body = json.dumps({"request_id": request_id, "name": "sample"}).encode()
    return call(
        app,
        "PUT",
        "/api/items/sample",
        query=f"ts={ts}",
        body=body,
        content_type="application/json",
        raw_headers=[("Content-Type", "application/json"), ("X-Api-Key", "k-123")],
    )


def interaction(request, response, delay_ms=0):
    return {"request": request, "response": response, "meta": {"delay_ms": delay_ms}}


def app_for(*interactions, **fields):
    data = {"httpmocker_cassette": 1, "interactions": list(interactions)}
    data.update(fields)
    return create_replay_app(Cassette(**data))


GET_A = {"method": "GET", "path": "/a"}
OK = {"status": 200, "headers": {"content-type": "application/json"}, "body": {"ok": True}}


class TestExampleScenario:
    """The shipped example replays its whole scenario in order."""

    def test_round_trip(self):
        app = example_app()
        first = put_item(app)
        assert (first.status, first.json) == (201, {"id": 7, "name": "sample"})
        assert first.header("Content-Type") == "application/json"
        second = put_item(app, ts="1761050009", request_id="r-10")
        assert (second.status, second.json) == (409, {"error": "item already exists", "id": 7})

        start = time.monotonic()
        listed = call(app, "GET", "/api/items")
        assert time.monotonic() - start >= 0.25  # the hand-added delay_ms
        assert (listed.status, listed.json) == (200, [{"id": 7, "name": "sample"}])

        for _ in range(2):
            deleted = call(app, "DELETE", "/api/items/sample")
            assert (deleted.status, deleted.body) == (204, b"")
            assert deleted.header("Content-Type") is None

        assert call(app, "GET", "/api/items").json == []
        health = call(app, "GET", "/api/health")
        assert (health.status, health.body) == (200, b"ok")
        assert health.header("Content-Type") == "text/plain; charset=utf-8"

        journal = journal_of(app)
        assert journal["counts"]["served"] == 7
        assert journal["counts"]["miss"] == 0
        assert journal["counts"]["exhausted"] == 0
        assert [e["interaction_index"] for e in journal["entries"]] == [0, 1, 2, 3, 4, 5, 6]

    def test_exhausted(self):
        app = example_app()
        put_item(app)
        put_item(app)
        third = put_item(app)
        assert third.status == 500
        assert third.header("X-Httpmocker") == "exhausted"
        assert third.json == {
            "httpmocker": "exhausted",
            "key": {
                "method": "PUT",
                "path": "/api/items/sample",
                "query": {},
                "body": {"name": "sample"},
            },
        }
        journal = journal_of(app)
        assert journal["counts"]["exhausted"] == 1
        entry = journal["entries"][-1]
        assert (entry["outcome"], entry["status"]) == ("exhausted", 500)
        assert entry["key"] == third.json["key"]
        assert "interaction_index" not in entry

    def test_miss(self):
        app = example_app()
        r = call(app, "PUT", "/api/items/other")
        assert r.status == 500
        assert r.header("X-Httpmocker") == "miss"
        assert r.json["httpmocker"] == "miss"
        assert r.json["key"] == {"method": "PUT", "path": "/api/items/other", "query": {}}
        assert journal_of(app)["counts"]["miss"] == 1

    def test_reset_rewinds_the_sequences(self):
        app = example_app()
        put_item(app)
        put_item(app)
        assert call(app, "DELETE", "/_httpmocker/journal").status == 204
        assert put_item(app).status == 201
        assert journal_of(app)["counts"] == {
            "served": 1,
            "recorded": 0,
            "miss": 0,
            "exhausted": 0,
            "upstream_error": 0,
            "rejected": 0,
            "unmatched": 0,
        }

    def test_without_reset_the_scenario_is_exhausted(self):
        app = example_app()
        put_item(app)
        put_item(app)
        assert put_item(app).status == 500

    def test_reserved_prefix(self):
        app = example_app()
        r = call(app, "GET", "/_httpmocker/other")
        assert (r.status, r.header("X-Httpmocker")) == (404, "reserved")
        assert journal_of(app)["counts"]["rejected"] == 1

    def test_given_journal_is_used(self):
        journal = Journal()
        with open(EXAMPLE, encoding="utf-8") as f:
            app = create_replay_app(Cassette(**json.load(f)), journal)
        call(app, "GET", "/api/health")
        assert journal.snapshot()["counts"]["served"] == 1


class TestIdentity:
    def test_without_ignore_lists_changed_values_miss(self):
        recorded = {
            "method": "PUT",
            "path": "/v1/items",
            "query": {"ts": ["1"]},
            "headers": {"content-type": "application/json"},
            "body": {"name": "a", "request_id": "r-1"},
        }
        app = app_for(interaction(recorded, OK))
        body = json.dumps({"name": "a", "request_id": "r-2"}).encode()
        r = call(app, "PUT", "/v1/items", query="ts=2", body=body, content_type="application/json")
        assert r.status == 500
        assert journal_of(app)["counts"]["miss"] == 1

    def test_json_body_matches_canonically(self):
        recorded = {
            "method": "POST",
            "path": "/a",
            "headers": {"content-type": "application/json"},
            "body": {"b": [1, 2], "a": "x"},
        }
        app = app_for(interaction(recorded, OK), interaction(recorded, OK))
        r = call(
            app, "POST", "/a", body=b'{ "a" : "x",\n "b": [1,2] }', content_type="application/json"
        )
        assert r.status == 200
        r = call(
            app, "POST", "/a", body=b'{"a": "x", "b": [1.0, 2]}', content_type="application/json"
        )
        assert r.status == 500  # 1 and 1.0 key differently

    def test_hand_added_json_body_without_content_type(self):
        recorded = {"method": "POST", "path": "/a", "body": {"a": 1}}
        app = app_for(interaction(recorded, OK))
        assert (
            call(app, "POST", "/a", body=b'{"a":1}', content_type="application/json").status == 200
        )

    def test_text_body_matches_byte_for_byte(self):
        recorded = {"method": "POST", "path": "/a", "body_text": "x=1&y=2"}
        app = app_for(interaction(recorded, OK), interaction(recorded, OK))
        form = "application/x-www-form-urlencoded"
        assert call(app, "POST", "/a", body=b"x=1&y=2", content_type=form).status == 200
        assert call(app, "POST", "/a", body=b"y=2&x=1", content_type=form).status == 500

    def test_query_order_and_encoding_do_not_matter(self):
        recorded = {"method": "GET", "path": "/s", "query": {"q": ["a b"], "page": ["2"]}}
        app = app_for(interaction(recorded, OK))
        assert call(app, "GET", "/s", query="page=2&q=a+b").status == 200

    @pytest.mark.parametrize(
        ("raw_headers", "status"),
        [
            ([("User-Agent", "suite/1")], 200),
            ([("user-agent", "suite/2")], 500),
            ([], 500),
        ],
    )
    def test_match_headers(self, raw_headers, status):
        recorded = {"method": "GET", "path": "/a", "headers": {"user-agent": "suite/1"}}
        app = app_for(interaction(recorded, OK), identity={"match_headers": ["user-agent"]})
        assert call(app, "GET", "/a", raw_headers=raw_headers).status == status

    def test_headers_do_not_match_by_default(self):
        recorded = {"method": "GET", "path": "/a", "headers": {"user-agent": "suite/1"}}
        app = app_for(interaction(recorded, OK))
        assert call(app, "GET", "/a", raw_headers=[("User-Agent", "other")]).status == 200

    def test_absent_header_matches_absent(self):
        app = app_for(interaction(GET_A, OK), identity={"match_headers": ["x-tenant"]})
        assert call(app, "GET", "/a", raw_headers=[]).status == 200
        assert call(app, "GET", "/a", raw_headers=[("X-Tenant", "")]).status == 500

    def test_repeated_header_matches_value_by_value(self):
        recorded = {"method": "GET", "path": "/a", "headers": {"x-rep": ["1", "2"]}}
        app = app_for(
            interaction(recorded, OK),
            interaction(recorded, OK),
            identity={"match_headers": ["x-rep"]},
        )
        assert call(app, "GET", "/a", raw_headers=[("X-Rep", "1"), ("X-Rep", "2")]).status == 200
        miss = call(app, "GET", "/a", raw_headers=[("X-Rep", "1,2")])
        assert miss.status == 500
        assert miss.json["key"]["headers"] == {"x-rep": "1,2"}

    def test_redacted_query_param_is_out_of_the_key(self):
        recorded = {"method": "GET", "path": "/a", "query": {"api_key": ["<redacted>"], "p": ["1"]}}
        app = app_for(interaction(recorded, OK), redacted_query_params=["api_key"])
        r = call(app, "GET", "/a", query="api_key=s3cret&p=1")
        assert r.status == 200
        entry = journal_of(app)["entries"][0]
        digest = hashlib.sha256(b"s3cret").hexdigest()[:12]
        assert entry["request"]["query"]["api_key"] == [f"<redacted:{digest}>"]


class TestServing:
    def test_sequence_positions_on_the_console(self, capsys):
        app = app_for(
            interaction(GET_A, {"status": 200, "body_text": "one"}),
            interaction({"method": "GET", "path": "/b"}, OK),
            interaction(GET_A, {"status": 200, "body_text": "two"}),
        )
        assert call(app, "GET", "/a").body == b"one"
        assert call(app, "GET", "/a").body == b"two"
        out = capsys.readouterr().out
        assert "GET /a → 200 served #0 (1/2)" in out
        assert "GET /a → 200 served #2 (2/2)" in out

    def test_miss_stands_out_on_the_console(self, capsys):
        app = app_for(interaction(GET_A, OK))
        call(app, "GET", "/nope")
        call(app, "GET", "/a")
        call(app, "GET", "/a")
        out = capsys.readouterr().out
        assert "MISS: GET /nope → 500" in out
        assert "EXHAUSTED: GET /a → 500, all 1 recorded response(s)" in out

    def test_body_representations_are_served_as_bytes(self):
        app = app_for(
            interaction(GET_A, {"status": 200, "body_base64": "AAEC/w=="}),
            interaction(GET_A, {"status": 200, "body": {"b": 1, "a": "zażółć"}}),
            interaction(GET_A, {"status": 200}),
        )
        assert call(app, "GET", "/a").body == b"\x00\x01\x02\xff"
        assert call(app, "GET", "/a").body == '{"a":"zażółć","b":1}'.encode()
        assert call(app, "GET", "/a").body == b""

    def test_headers_as_recorded_and_no_default_content_type(self):
        response = {"status": 201, "headers": {"x-rep": ["1", "2"], "location": "/v1/items/7"}}
        r = call(app_for(interaction(GET_A, response)), "GET", "/a")
        assert r.status == 201
        assert r.header("X-Rep") == ["1", "2"]
        assert r.header("Location") == "/v1/items/7"
        assert r.header("Content-Type") is None

    def test_head_keeps_the_recorded_content_length(self):
        recorded = {"method": "HEAD", "path": "/a"}
        response = {
            "status": 200,
            "headers": {"content-type": "application/json", "content-length": "17"},
        }
        r = call(app_for(interaction(recorded, response)), "HEAD", "/a")
        assert r.status == 200
        assert r.body == b""
        assert r.header("Content-Length") == "17"

    def test_root_path(self):
        app = app_for(interaction({"method": "GET", "path": "/"}, OK))
        assert call(app, "GET", "/").json == {"ok": True}

    def test_unknown_recorded_status(self):
        assert call(app_for(interaction(GET_A, {"status": 599})), "GET", "/a").status == 599

    def test_delay(self):
        app = app_for(interaction(GET_A, OK, delay_ms=120))
        start = time.monotonic()
        assert call(app, "GET", "/a").status == 200
        assert time.monotonic() - start >= 0.12


class TestRedaction:
    def test_redacted_response_header_is_omitted(self):
        response = {"status": 200, "headers": {"set-cookie": "<redacted>", "x-trace": "t"}}
        r = call(app_for(interaction(GET_A, response)), "GET", "/a")
        assert r.header("Set-Cookie") is None
        assert r.header("X-Trace") == "t"

    def test_header_the_cassette_does_not_redact_is_sent(self):
        """A hand edit can put a real set-cookie back for replay."""
        response = {"status": 200, "headers": {"set-cookie": "sid=abc"}}
        app = app_for(interaction(GET_A, response), redacted_headers=["x-api-key"])
        assert call(app, "GET", "/a").header("Set-Cookie") == "sid=abc"

    def test_display_redacts_the_defaults_even_when_the_cassette_does_not(self, capsys):
        app = app_for(interaction(GET_A, OK), redacted_headers=["x-api-key"])
        call(
            app,
            "GET",
            "/a",
            raw_headers=[("Authorization", "Bearer s3cret"), ("X-Api-Key", "k-1")],
        )
        entry = journal_of(app)["entries"][0]
        assert entry["request"]["headers"]["authorization"].startswith("<redacted:")
        assert entry["request"]["headers"]["x-api-key"].startswith("<redacted:")
        assert "s3cret" not in capsys.readouterr().out


class TestPlayer:
    def test_next_and_rewind(self):
        cassette = Cassette(
            httpmocker_cassette=1,
            interactions=[interaction(GET_A, OK), interaction(GET_A, OK)],
        )
        player = Player(cassette)
        key = next(iter(player._sequences))
        assert player.next(key) == ("served", 0, 1, 2)
        assert player.next(key) == ("served", 1, 2, 2)
        assert player.next(key) == ("exhausted", None, 2, 2)
        player.rewind()
        assert player.next(key) == ("served", 0, 1, 2)
