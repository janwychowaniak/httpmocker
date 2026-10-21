"""Tests for body representation in bodies (cassettes and the journal)."""

import base64
import json

import pytest

from httpmocker.bodies import is_json_media_type, represent_body


class TestIsJsonMediaType:
    @pytest.mark.parametrize(
        "content_type",
        [
            "application/json",
            "application/json; charset=utf-8",
            "Application/JSON",
            "application/problem+json",
            "application/vnd.api+json; ext=bulk",
        ],
    )
    def test_json_types(self, content_type):
        assert is_json_media_type(content_type)

    @pytest.mark.parametrize(
        "content_type", [None, "", "text/plain", "application/jsonp", "text/json+x", "json"]
    )
    def test_other_types(self, content_type):
        assert not is_json_media_type(content_type)


class TestRepresentBody:
    def test_empty_body_has_no_representation(self):
        assert represent_body(b"", "application/json") == {}

    def test_json_body_is_parsed(self):
        data = json.dumps({"name": "sample", "n": [1, 2.5]}).encode()
        assert represent_body(data, "application/json") == {
            "body": {"name": "sample", "n": [1, 2.5]}
        }

    def test_plus_json_body_is_parsed(self):
        assert represent_body(b"[1, 2]", "application/problem+json") == {"body": [1, 2]}

    def test_json_looking_body_without_json_type_is_text(self):
        assert represent_body(b'{"a": 1}', "text/plain") == {"body_text": '{"a": 1}'}

    def test_json_body_without_content_type_is_text(self):
        assert represent_body(b'{"a": 1}', None) == {"body_text": '{"a": 1}'}

    def test_malformed_json_is_text(self):
        assert represent_body(b'{"name": ', "application/json") == {"body_text": '{"name": '}

    @pytest.mark.parametrize("data", [b"1e400", b"NaN", b'{"x": -Infinity}'])
    def test_json_that_cannot_be_served_back_as_json_is_text(self, data):
        """json.loads takes these, but they would dump as NaN or Infinity."""
        assert represent_body(data, "application/json") == {"body_text": data.decode()}

    def test_too_deeply_nested_json_is_text(self):
        data = b"[" * 100_000 + b"]" * 100_000
        assert represent_body(data, "application/json") == {"body_text": data.decode()}

    def test_utf8_text_is_kept_as_text(self):
        assert represent_body("zażółć".encode(), "text/plain; charset=latin-1") == {
            "body_text": "zażółć"
        }

    def test_non_utf8_body_is_base64(self):
        data = b"\xff\xfe\x00binary"
        assert represent_body(data, "application/octet-stream") == {
            "body_base64": base64.b64encode(data).decode("ascii")
        }

    def test_non_utf8_body_declared_json_is_base64(self):
        data = b'{"a": "\xff"}'
        assert "body_base64" in represent_body(data, "application/json")
