"""Tests for the cassette model, its validation and loading (docs/record-replay.md §9)."""

import copy
import json
from pathlib import Path

import pytest

from httpmocker.cassette import Cassette, load_cassette

EXAMPLE = Path(__file__).resolve().parent.parent / "cassettes" / "example.json"


def interaction(method="PUT", path="/v1/items", status=201, **parts):
    """A minimal valid interaction; ``parts`` overrides request/response/meta."""
    result = {
        "request": {"method": method, "path": path},
        "response": {"status": status},
    }
    result.update(parts)
    return result


def cassette(**fields):
    """A minimal valid cassette; ``fields`` overrides top-level fields."""
    result = {"httpmocker_cassette": 1, "interactions": [interaction()]}
    result.update(fields)
    return result


def assert_invalid(data, message):
    with pytest.raises(ValueError, match=message):
        Cassette(**data)


class TestValidCassettes:
    def test_minimal_cassette_and_its_defaults(self):
        c = Cassette(**cassette())
        assert c.tool_version is None
        assert c.target is None
        assert c.identity.ignore_query_params == []
        assert c.identity.ignore_json_paths == []
        assert c.identity.match_headers == []
        assert c.redacted_headers == [
            "authorization",
            "proxy-authorization",
            "cookie",
            "set-cookie",
        ]
        assert c.redacted_query_params == []
        assert c.interactions[0].meta.delay_ms == 0
        assert c.interactions[0].request.query == {}
        assert c.interactions[0].request.headers == {}

    def test_shipped_example_is_valid(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            c = Cassette(**json.load(f))
        assert len(c.interactions) == 7
        assert c.identity.ignore_json_paths == ["/request_id"]
        statuses = [i.response.status for i in c.interactions if i.request.method == "PUT"]
        assert statuses == [201, 409]

    def test_full_interaction(self):
        data = cassette(
            interactions=[
                interaction(
                    request={
                        "method": "PUT",
                        "path": "/v1/items",
                        "query": {"ts": ["1"], "flag": [""]},
                        "headers": {"content-type": "application/json", "x-rep": ["1", "2"]},
                        "body": {"name": "sample"},
                    },
                    response={
                        "status": 201,
                        "headers": {"content-type": "application/json"},
                        "body": {"id": 7},
                    },
                    meta={
                        "recorded_at": "2026-10-21T12:00:00.000Z",
                        "duration_ms": 12,
                        "delay_ms": 300,
                    },
                )
            ]
        )
        c = Cassette(**data)
        assert c.interactions[0].request.headers["x-rep"] == ["1", "2"]
        assert c.interactions[0].meta.delay_ms == 300

    @pytest.mark.parametrize(
        "body", [{"body_text": "ok"}, {"body_base64": "AAEC/w=="}, {"body": None}, {"body": []}]
    )
    def test_one_body_representation(self, body):
        Cassette(**cassette(interactions=[interaction(response={"status": 200, **body})]))

    def test_head_response_keeps_content_length(self):
        data = cassette(
            interactions=[
                interaction("HEAD", response={"status": 200, "headers": {"content-length": "17"}})
            ]
        )
        assert Cassette(**data).interactions[0].response.headers == {"content-length": "17"}


class TestInvalidCassettes:
    @pytest.mark.parametrize("version", [0, 2])
    def test_format_version_must_be_1(self, version):
        assert_invalid(cassette(httpmocker_cassette=version), "httpmocker_cassette must be 1")

    def test_format_version_is_required(self):
        data = cassette()
        del data["httpmocker_cassette"]
        assert_invalid(data, "httpmocker_cassette")

    def test_at_least_one_interaction(self):
        assert_invalid(cassette(interactions=[]), "At least one interaction")

    @pytest.mark.parametrize(
        "path",
        [
            ["delay"],
            ["meta", "delay"],
            ["request", "bodytext"],
            ["response", "code"],
            ["identity", "ignore_headers"],
        ],
    )
    def test_unknown_fields_are_errors(self, path):
        data = cassette()
        target = data if path[0] in ("delay", "identity") else data["interactions"][0]
        if path[0] == "identity":
            data["identity"] = {path[1]: []}
        elif len(path) == 1:
            data[path[0]] = 1
        else:
            target.setdefault(path[0], {})[path[1]] = 1
        assert_invalid(data, "Extra inputs are not permitted")

    @pytest.mark.parametrize(
        ("method", "message"), [("get", "must be uppercase"), ("TRACE", "must be one of")]
    )
    def test_method(self, method, message):
        assert_invalid(cassette(interactions=[interaction(method)]), message)

    @pytest.mark.parametrize(
        ("path", "message"),
        [
            ("v1/items", "must start with '/'"),
            ("/v1/items?x=1", r"must not contain '\?' or '#'"),
            ("/v1#top", r"must not contain '\?' or '#'"),
            ("/_httpmocker/journal", "reserves for its journal"),
        ],
    )
    def test_path(self, path, message):
        assert_invalid(cassette(interactions=[interaction(path=path)]), message)

    @pytest.mark.parametrize("status", [99, 600])
    def test_status(self, status):
        assert_invalid(cassette(interactions=[interaction(status=status)]), "between 100-599")

    def test_negative_delay(self):
        data = cassette(interactions=[interaction(meta={"delay_ms": -1})])
        assert_invalid(data, "Delay must be non-negative")

    def test_negative_duration(self):
        data = cassette(interactions=[interaction(meta={"duration_ms": -5})])
        assert_invalid(data, "Duration must be non-negative")

    def test_two_body_representations(self):
        data = cassette(
            interactions=[interaction(response={"status": 200, "body": {}, "body_text": "{}"})]
        )
        assert_invalid(data, "Only one of body, body_text and body_base64")

    def test_json_null_body_counts_as_a_representation(self):
        data = cassette(
            interactions=[interaction(response={"status": 200, "body": None, "body_text": ""})]
        )
        assert_invalid(data, "Only one of")

    def test_invalid_base64(self):
        data = cassette(interactions=[interaction(response={"status": 200, "body_base64": "a?b"})])
        assert_invalid(data, "not valid base64")

    def test_upper_case_header_name(self):
        data = cassette(
            interactions=[
                interaction(
                    request={"method": "GET", "path": "/a", "headers": {"Content-Type": "x"}}
                )
            ]
        )
        assert_invalid(data, "Header names must be lower-case")

    def test_empty_list_for_a_header(self):
        data = cassette(interactions=[interaction(response={"status": 200, "headers": {"x": []}})])
        assert_invalid(data, "empty list of values")

    @pytest.mark.parametrize("header", ["host", "content-length", "connection"])
    def test_transport_header_in_request(self, header):
        data = cassette(
            interactions=[
                interaction(request={"method": "GET", "path": "/a", "headers": {header: "x"}})
            ]
        )
        assert_invalid(data, "Request has transport headers")

    @pytest.mark.parametrize(
        ("method", "header"),
        [("GET", "content-length"), ("HEAD", "transfer-encoding"), ("GET", "proxy-authenticate")],
    )
    def test_transport_header_in_response(self, method, header):
        data = cassette(
            interactions=[interaction(method, response={"status": 200, "headers": {header: "1"}})]
        )
        assert_invalid(data, "Response has transport headers")

    @pytest.mark.parametrize("pointer", ["", "request_id", "/a~2"])
    def test_ignore_json_path_must_be_a_pointer(self, pointer):
        assert_invalid(cassette(identity={"ignore_json_paths": [pointer]}), "JSON Pointer")

    def test_match_header_names_lower_case(self):
        assert_invalid(
            cassette(identity={"match_headers": ["User-Agent"]}),
            "match_headers names must be lower-case",
        )

    def test_match_header_cannot_be_transport(self):
        assert_invalid(
            cassette(identity={"match_headers": ["host"]}), "cannot hold transport headers"
        )

    @pytest.mark.parametrize(
        ("redacted", "match"),
        [(None, "authorization"), (["x-api-key"], "x-api-key")],
    )
    def test_match_header_cannot_be_redacted(self, redacted, match):
        fields = {"identity": {"match_headers": [match]}}
        if redacted is not None:
            fields["redacted_headers"] = redacted
        assert_invalid(cassette(**fields), "cannot hold redacted headers")

    def test_redacted_header_names_lower_case(self):
        assert_invalid(cassette(redacted_headers=["Authorization"]), "redacted_headers names")


class TestLoadCassette:
    def write(self, tmp_path, content):
        path = tmp_path / "cassette.json"
        path.write_text(content if isinstance(content, str) else json.dumps(content))
        return str(path)

    def test_valid_file(self, tmp_path):
        c = load_cassette(self.write(tmp_path, cassette()))
        assert len(c.interactions) == 1

    def test_example_file(self):
        assert len(load_cassette(str(EXAMPLE)).interactions) == 7

    @pytest.mark.parametrize(
        ("content", "message"),
        [
            ("{ not json", "Invalid JSON in cassette file"),
            ("[]", "the file must hold a JSON object"),
            (cassette(interactions=[]), "Invalid cassette"),
        ],
    )
    def test_invalid_files_exit_1(self, tmp_path, capsys, content, message):
        with pytest.raises(SystemExit) as exc_info:
            load_cassette(self.write(tmp_path, copy.deepcopy(content)))
        assert exc_info.value.code == 1
        assert message in capsys.readouterr().out

    def test_missing_file_exits_1(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as exc_info:
            load_cassette(str(tmp_path / "nope.json"))
        assert exc_info.value.code == 1
        assert "Cassette file not found" in capsys.readouterr().out

    def test_unreadable_file_exits_1(self, tmp_path, capsys):
        path = tmp_path / "latin1.json"
        path.write_bytes(b'{"x": "\xff"}')
        with pytest.raises(SystemExit) as exc_info:
            load_cassette(str(path))
        assert exc_info.value.code == 1
        assert "Could not read cassette file" in capsys.readouterr().out
