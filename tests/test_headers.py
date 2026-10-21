"""Tests for header helpers in headers."""

from httpmocker.headers import (
    RAW_HEADERS_KEY,
    TRANSPORT_HEADERS,
    header_mapping,
    request_header_pairs,
    transport_header_names,
)


class TestRequestHeaderPairs:
    def test_raw_pairs_are_used_when_present(self):
        raw = [("X-Rep", "1"), ("X-Rep", "2")]
        environ = {RAW_HEADERS_KEY: raw, "HTTP_X_REP": "1,2"}
        assert request_header_pairs(environ) == raw

    def test_pairs_are_rebuilt_from_the_environ_otherwise(self):
        environ = {
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": "2",
            "HTTP_X_API_KEY": "k",
            "HTTP_HOST": "mock.test",
            "PATH_INFO": "/a",
        }
        assert sorted(request_header_pairs(environ)) == [
            ("Content-Length", "2"),
            ("Content-Type", "application/json"),
            ("Host", "mock.test"),
            ("X-Api-Key", "k"),
        ]

    def test_empty_content_type_and_length_are_left_out(self):
        assert request_header_pairs({"CONTENT_TYPE": "", "CONTENT_LENGTH": ""}) == []


class TestTransportHeaderNames:
    def test_fixed_set_without_connection(self):
        assert transport_header_names([("Accept", "*/*")]) == TRANSPORT_HEADERS

    def test_headers_named_by_connection_are_added(self):
        names = transport_header_names([("Connection", "close, X-Hop ,  "), ("connection", "Foo")])
        assert {"x-hop", "foo"} <= names
        assert names >= TRANSPORT_HEADERS


class TestHeaderMapping:
    def test_names_are_lower_cased_and_repeats_listed_in_order(self):
        pairs = [("X-Rep", "1"), ("Accept", "*/*"), ("x-rep", "2"), ("X-REP", "3")]
        assert header_mapping(pairs) == {"x-rep": ["1", "2", "3"], "accept": "*/*"}

    def test_excluded_names_are_left_out(self):
        pairs = [("Host", "h"), ("Accept", "*/*")]
        assert header_mapping(pairs, exclude=frozenset({"host"})) == {"accept": "*/*"}
