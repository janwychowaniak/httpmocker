"""Tests for request identity helpers in identity."""

import pytest

from httpmocker.identity import parse_json_pointer


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
