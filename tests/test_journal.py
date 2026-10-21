"""Tests for the journal: entries, counts, the cap and request descriptions."""

import hashlib
import io
import json
import re

import pytest
from bottle import BaseRequest

from httpmocker.headers import RAW_HEADERS_KEY
from httpmocker.journal import (
    MAX_BODY_BYTES,
    OUTCOMES,
    Journal,
    describe_request,
    is_reserved_path,
    timestamp,
)
from httpmocker.redaction import Redaction


def make_request(method="POST", query="", body=b"", content_type=None, raw_headers=None):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": "/v1/items",
        "QUERY_STRING": query,
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
    }
    if content_type:
        environ["CONTENT_TYPE"] = content_type
    if raw_headers is not None:
        environ[RAW_HEADERS_KEY] = raw_headers
    return BaseRequest(environ)


def add(journal, outcome="served", status=200):
    journal.add({"method": "GET", "path": "/a"}, "2026-10-21T12:00:00.000Z", outcome, status)


class TestIsReservedPath:
    @pytest.mark.parametrize(
        "path", ["/_httpmocker", "/_httpmocker/", "/_httpmocker/journal", "/_httpmocker/journal/x"]
    )
    def test_reserved(self, path):
        assert is_reserved_path(path)

    @pytest.mark.parametrize("path", ["/", "/_httpmockerX", "/_httpmocker-x", "/api/_httpmocker"])
    def test_not_reserved(self, path):
        assert not is_reserved_path(path)


class TestJournal:
    def test_empty_snapshot(self):
        assert Journal().snapshot() == {
            "counts": dict.fromkeys(OUTCOMES, 0),
            "dropped": 0,
            "entries": [],
        }

    def test_entries_and_counts(self):
        journal = Journal()
        add(journal, "served", 201)
        add(journal, "unmatched", 404)
        journal.add({"method": "PUT"}, "t", "served", 409, interaction_index=3)
        snapshot = journal.snapshot()
        assert snapshot["counts"]["served"] == 2
        assert snapshot["counts"]["unmatched"] == 1
        assert [e["seq"] for e in snapshot["entries"]] == [1, 2, 3]
        assert [e["status"] for e in snapshot["entries"]] == [201, 404, 409]
        assert "interaction_index" not in snapshot["entries"][0]
        assert snapshot["entries"][2]["interaction_index"] == 3
        assert snapshot["entries"][0] == {
            "seq": 1,
            "received_at": "2026-10-21T12:00:00.000Z",
            "request": {"method": "GET", "path": "/a"},
            "outcome": "served",
            "status": 201,
        }

    def test_unknown_outcome_is_refused(self):
        with pytest.raises(ValueError, match="Unknown journal outcome"):
            add(Journal(), "nope")

    def test_cap_keeps_the_last_entries_and_counts_running_totals(self):
        journal = Journal(max_entries=3)
        for _ in range(5):
            add(journal)
        snapshot = journal.snapshot()
        assert snapshot["dropped"] == 2
        assert snapshot["counts"]["served"] == 5
        assert [e["seq"] for e in snapshot["entries"]] == [3, 4, 5]
        assert snapshot["entries"][0]["seq"] == snapshot["dropped"] + 1

    def test_reset_zeroes_everything(self):
        journal = Journal(max_entries=2)
        for _ in range(3):
            add(journal)
        journal.reset()
        assert journal.snapshot() == Journal().snapshot()
        add(journal)
        assert journal.snapshot()["entries"][0]["seq"] == 1

    def test_snapshot_is_a_copy(self):
        journal = Journal()
        snapshot = journal.snapshot()
        add(journal)
        assert snapshot["entries"] == []
        assert snapshot["counts"]["served"] == 0


class TestTimestamp:
    def test_utc_iso_with_milliseconds(self):
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", timestamp())


class TestDescribeRequest:
    def test_headers_are_lower_cased_listed_and_stripped_of_transport(self):
        request = make_request(
            raw_headers=[
                ("Host", "mock.test"),
                ("Content-Length", "0"),
                ("Connection", "keep-alive, X-Hop"),
                ("X-Hop", "1"),
                ("Accept", "*/*"),
                ("X-Rep", "1"),
                ("x-rep", "2"),
            ]
        )
        described = describe_request(request, "/v1/items", Redaction())
        assert described["headers"] == {"accept": "*/*", "x-rep": ["1", "2"]}

    def test_redacted_header_values_are_hashed(self):
        request = make_request(raw_headers=[("Authorization", "Bearer s3cret"), ("X-Api-Key", "k")])
        described = describe_request(
            request, "/v1/items", Redaction(headers=frozenset({"authorization", "x-api-key"}))
        )
        digest = hashlib.sha256(b"Bearer s3cret").hexdigest()[:12]
        assert described["headers"]["authorization"] == f"<redacted:{digest}>"
        assert "s3cret" not in json.dumps(described)
        assert described["headers"]["x-api-key"].startswith("<redacted:")

    def test_query_is_a_multimap_with_blank_values_and_redaction(self):
        request = make_request(query="flag&x=1&x=2&empty=&q=a+b%20c&api_key=s3cret")
        described = describe_request(
            request, "/v1/items", Redaction(query_params=frozenset({"api_key"}))
        )
        assert described["query"]["flag"] == [""]
        assert described["query"]["x"] == ["1", "2"]
        assert described["query"]["empty"] == [""]
        assert described["query"]["q"] == ["a b c"]
        assert described["query"]["api_key"][0].startswith("<redacted:")

    def test_method_and_path(self):
        described = describe_request(make_request("DELETE"), "/v1/a/b", Redaction())
        assert described["method"] == "DELETE"
        assert described["path"] == "/v1/a/b"

    def test_json_body(self):
        body = json.dumps({"name": "sample"}).encode()
        request = make_request(body=body, content_type="application/json")
        described = describe_request(request, "/v1/items", Redaction())
        assert described["body"] == {"name": "sample"}
        assert described["body_size"] == len(body)
        assert "body_omitted" not in described

    def test_no_body(self):
        described = describe_request(make_request("GET"), "/v1/items", Redaction())
        assert described["body_size"] == 0
        assert not {"body", "body_text", "body_base64", "body_omitted"} & described.keys()

    def test_body_at_the_limit_is_kept(self):
        body = b"x" * MAX_BODY_BYTES
        described = describe_request(make_request(body=body), "/v1/items", Redaction())
        assert described["body_text"] == body.decode()
        assert described["body_size"] == MAX_BODY_BYTES

    def test_body_over_the_limit_is_omitted(self):
        body = b"x" * (MAX_BODY_BYTES + 1)
        described = describe_request(make_request(body=body), "/v1/items", Redaction())
        assert described["body_omitted"] is True
        assert described["body_size"] == MAX_BODY_BYTES + 1
        assert "body_text" not in described

    def test_body_stays_readable_afterwards(self):
        request = make_request(body=b"payload")
        describe_request(request, "/v1/items", Redaction())
        assert request.body.read() == b"payload"
