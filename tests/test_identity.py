"""Tests for request identity in identity (docs/record-replay.md §10)."""

import copy

import pytest

from httpmocker.identity import (
    RequestKey,
    body_key,
    delete_pointer,
    headers_key,
    parse_json_pointer,
    parse_query,
    query_key,
    render_key,
)


class TestParseJsonPointer:
    @pytest.mark.parametrize(
        ("pointer", "tokens"),
        [
            ("/request_id", ["request_id"]),
            ("/items/0/id", ["items", "0", "id"]),
            ("/", [""]),
            ("/a~1b", ["a/b"]),
            ("/m~0n", ["m~n"]),
            ("/~01", ["~1"]),
        ],
    )
    def test_valid_pointers(self, pointer, tokens):
        assert parse_json_pointer(pointer) == tokens

    @pytest.mark.parametrize("pointer", ["", "request_id", "#/request_id"])
    def test_pointer_must_start_with_a_slash(self, pointer):
        with pytest.raises(ValueError, match="must start with '/'"):
            parse_json_pointer(pointer)

    @pytest.mark.parametrize("pointer", ["/a~2b", "/a~", "/~/b"])
    def test_invalid_escape(self, pointer):
        with pytest.raises(ValueError, match="invalid escape"):
            parse_json_pointer(pointer)


class TestDeletePointer:
    @pytest.mark.parametrize(
        ("pointer", "expected"),
        [
            ("/request_id", {"items": [{"id": 1, "x": 2}, {"id": 3}], "meta": {"ts": 9}}),
            ("/meta/ts", {"request_id": "r", "items": [{"id": 1, "x": 2}, {"id": 3}], "meta": {}}),
            ("/items/0/x", {"request_id": "r", "items": [{"id": 1}, {"id": 3}], "meta": {"ts": 9}}),
            ("/items/1", {"request_id": "r", "items": [{"id": 1, "x": 2}], "meta": {"ts": 9}}),
        ],
    )
    def test_deletes_what_the_pointer_points_at(self, pointer, expected):
        document = {"request_id": "r", "items": [{"id": 1, "x": 2}, {"id": 3}], "meta": {"ts": 9}}
        delete_pointer(document, pointer)
        assert document == expected

    @pytest.mark.parametrize(
        "pointer",
        ["/nope", "/meta/nope/deeper", "/items/2", "/items/-", "/items/01", "/request_id/x"],
    )
    def test_unresolved_pointer_changes_nothing(self, pointer):
        document = {"request_id": "r", "items": [{"id": 1}, {"id": 3}], "meta": {"ts": 9}}
        before = copy.deepcopy(document)
        delete_pointer(document, pointer)
        assert document == before

    def test_escaped_and_empty_names(self):
        document = {"a/b": 1, "m~n": 2, "": 3}
        delete_pointer(document, "/a~1b")
        delete_pointer(document, "/m~0n")
        delete_pointer(document, "/")
        assert document == {}

    def test_scalar_document_is_left_alone(self):
        document = [1, 2]
        delete_pointer(document, "/x")
        assert document == [1, 2]


class TestBodyKey:
    def test_json_keys_canonically(self):
        a = body_key(b'{"b": 1, "a": [1, 2]}', "application/json", [])
        b = body_key(b'{"a":[1,2],"b":1}', "application/json; charset=utf-8", [])
        assert a == b == ("json", '{"a":[1,2],"b":1}')

    def test_round_trip_is_the_definition(self):
        assert body_key(b"1", "application/json", []) != body_key(b"1.0", "application/json", [])
        assert body_key(b"1.10", "application/json", []) == ("json", "1.1")

    def test_non_ascii_is_kept(self):
        assert body_key('"zażółć"'.encode(), "application/json", []) == ("json", '"zażółć"')

    def test_ignore_paths_apply_in_order(self):
        data = b'{"items": [{"id": 1}, {"id": 2}, {"id": 3}], "request_id": "r"}'
        key = body_key(data, "application/json", ["/request_id", "/items/0", "/items/0"])
        assert key == ("json", '{"items":[{"id":3}]}')

    @pytest.mark.parametrize(
        ("data", "content_type"),
        [
            (b'{"a": 1}', "text/plain"),
            (b'{"a": 1}', None),
            (b'{"a": ', "application/json"),
            (b"\xff", "application/json"),
            (b"", "application/json"),
        ],
    )
    def test_other_bodies_key_as_bytes(self, data, content_type):
        assert body_key(data, content_type, ["/a"]) == ("raw", data)

    def test_json_body_flag_ignores_the_media_type(self):
        assert body_key(b'{"a": 1}', None, [], json_body=True) == ("json", '{"a":1}')


class TestQueryKey:
    def test_sorted_by_name_then_value_without_left_out_names(self):
        pairs = parse_query("b=2&a=z&a=y&ts=1&flag&empty=")
        assert query_key(pairs, ["ts"]) == (
            ("a", "y"),
            ("a", "z"),
            ("b", "2"),
            ("empty", ""),
            ("flag", ""),
        )

    def test_plus_and_percent_20_are_both_a_space(self):
        assert query_key(parse_query("q=a+b"), []) == query_key(parse_query("q=a%20b"), [])


class TestHeadersKey:
    def test_absent_single_and_repeated(self):
        values = {"user-agent": ["ua"], "x-rep": ["1", "2"], "x-empty": [""]}
        assert headers_key(values, ["x-rep", "user-agent", "x-none", "x-empty"]) == (
            ("x-rep", ("1", "2")),
            ("user-agent", "ua"),
            ("x-none", None),
            ("x-empty", ""),
        )

    def test_nothing_to_match(self):
        assert headers_key({"user-agent": ["ua"]}, []) == ()


class TestRenderKey:
    def test_json_body_and_headers(self):
        key = RequestKey(
            "PUT",
            "/v1/items",
            (("a", "1"), ("a", "2"), ("b", "")),
            ("json", '{"name":"x"}'),
            (("user-agent", "ua"), ("x-none", None), ("x-rep", ("1", "2"))),
        )
        assert render_key(key) == {
            "method": "PUT",
            "path": "/v1/items",
            "query": {"a": ["1", "2"], "b": [""]},
            "body": {"name": "x"},
            "headers": {"user-agent": "ua", "x-none": None, "x-rep": ["1", "2"]},
        }

    def test_raw_bodies_and_no_headers(self):
        text = RequestKey("POST", "/a", (), ("raw", b"plain"), ())
        binary = RequestKey("POST", "/a", (), ("raw", b"\xff"), ())
        empty = RequestKey("GET", "/a", (), ("raw", b""), ())
        assert render_key(text) == {
            "method": "POST",
            "path": "/a",
            "query": {},
            "body_text": "plain",
        }
        assert render_key(binary)["body_base64"] == "/w=="
        assert render_key(empty) == {"method": "GET", "path": "/a", "query": {}}

    def test_json_key_that_is_not_json_any_more(self):
        """A JSON key holding an infinity is shown as text, so the answer stays JSON."""
        key = RequestKey("POST", "/a", (), ("json", "Infinity"), ())
        assert render_key(key)["body_text"] == "Infinity"
