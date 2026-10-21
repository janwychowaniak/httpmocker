"""Tests for value redaction in redaction."""

import hashlib

from httpmocker.redaction import (
    PLACEHOLDER,
    Redaction,
    console_placeholder,
    journal_placeholder,
)


def expected_hash(value):
    return f"<redacted:{hashlib.sha256(value.encode()).hexdigest()[:12]}>"


class TestPlaceholders:
    def test_journal_placeholder_is_the_sha256_prefix(self):
        assert journal_placeholder("Bearer s3cret") == expected_hash("Bearer s3cret")
        assert len(journal_placeholder("x")) == len("<redacted:>") + 12

    def test_console_placeholder(self):
        assert console_placeholder("anything") == PLACEHOLDER == "<redacted>"


class TestRedaction:
    def test_default_lists(self):
        redaction = Redaction()
        assert redaction.headers == {"authorization", "proxy-authorization", "cookie", "set-cookie"}
        assert redaction.query_params == frozenset()

    def test_headers_each_repeated_value_on_its_own(self):
        redaction = Redaction(headers=frozenset({"cookie", "authorization"}))
        mapping = {"cookie": ["a=1", "b=2"], "authorization": "Basic x", "accept": "*/*"}
        assert redaction.headers_of(mapping, journal_placeholder) == {
            "cookie": [expected_hash("a=1"), expected_hash("b=2")],
            "authorization": expected_hash("Basic x"),
            "accept": "*/*",
        }

    def test_query_params(self):
        redaction = Redaction(query_params=frozenset({"api_key"}))
        query = {"api_key": ["k1", "k2"], "page": ["2"]}
        assert redaction.query_of(query, console_placeholder) == {
            "api_key": [PLACEHOLDER, PLACEHOLDER],
            "page": ["2"],
        }

    def test_query_string_without_redacted_params_is_untouched(self):
        assert Redaction().query_string_of("a=%20b&c") == "a=%20b&c"

    def test_query_string_keeps_everything_else_as_received(self):
        redaction = Redaction(query_params=frozenset({"api_key", "flag"}))
        query_string = "x=a%2Fb&api%5Fkey=s3cret&flag&api_key=again&y=+1"
        assert redaction.query_string_of(query_string) == (
            "x=a%2Fb&api%5Fkey=<redacted>&flag=<redacted>&api_key=<redacted>&y=+1"
        )
