# Record, replay and the journal

**Status:** shipped in httpmocker 0.3.0. This document is the contract of the feature. The README's
"Record and replay" section is derived from it; where the two differ, this document wins. A change to
the feature updates this document in the same commit.

## 1. Purpose and scope

Two run modes join the existing config mode (`-c`):

- **record**: httpmocker listens as usual and forwards every request to one target base URL. It returns
  the target's response to the client and appends the request/response pair to a **cassette** file.
- **replay**: httpmocker serves a cassette. Each request is matched to a recorded interaction by an
  identity key, and **repeated identical requests receive the recorded responses in recorded order**.

A **journal** endpoint, in all three modes, lets tests assert on what the server received, and a reset
starts the next scenario.

The use case is an API that keeps state. A resource created by `PUT` answers `201` the first time and
`409` the second; a canned-response server cannot express that. A recording of the real conversation
carries the real responses, error bodies included, and replays them in order. The recording is the mock.

The tool stays a canned-response server: no data store, no rules, no templating, no state names. The
cassette is a configuration file the tool writes itself, and "statefulness" is the recorded order and
nothing more.

There is no new runtime dependency: the standard library on the existing Bottle and wsgiref stack covers
everything. The Docker image gains no files.

## 2. Design decisions

| Decision | Reason |
|---|---|
| Recorded order per request is the only "state"; real state (store + rules) is rejected. | The tool stays simple and does not duplicate the rule engines, data buckets or templating of the large mock servers. |
| The end of a recorded sequence is an **error**, not a repeat of the last response. | Determinism: a test that calls more often than it recorded must fail. |
| The cassette is a separate file type. The endpoint config is not extended and stays valid unchanged, apart from the reserved prefix `/_httpmocker` (§13). | One format per purpose, no migration for existing users. |
| Identity and redaction lists are stored in the cassette at record time and read from it at replay. | A cassette is self-describing; replay needs no flags. |
| Only two modes. Replay never falls back to the target. | A replay that silently fills gaps from the live system hides an incomplete cassette. A cassette grows by recording again. |
| Upstream TLS trusts the system trust store, i.e. the OpenSSL default paths of the Python build. Only `SSL_CERT_FILE` / `SSL_CERT_DIR` override it. No CA in the image, no `--insecure`, no `--ca-file`. | It is the same mechanism the Go tools in this field use. The CA belongs to the deployment (a mounted file and one environment variable), never to the image. |
| The default redaction list applies on the console and in the journal in **every** mode, config mode included. | One rule everywhere: no redacted value reaches stdout. |
| The journal keeps the last 10 000 entries, with counts as running totals and request bodies limited to 64 KiB. | A config-mode instance can run for weeks; the journal must not grow without bound. |
| No egress proxy: `HTTPS_PROXY`, `NO_PROXY` and CONNECT are not supported. | Nothing is built in reserve. |

## 3. Command line

```
httpmocker -p PORT -c CONFIG [--validate-config]                                  # config mode, unchanged
httpmocker -p PORT --record TARGET_BASE_URL --cassette FILE [--upstream-timeout SECONDS]
           [--ignore-query-param NAME]... [--ignore-json-path POINTER]... [--match-header NAME]...
           [--redact-header NAME]... [--redact-query-param NAME]...
httpmocker -p PORT --replay FILE [--validate-config]
```

**Mode selection**
- `-c`, `--record` and `--replay` are mutually exclusive, and exactly one is required.
- `--record` requires `--cassette`.
- `--cassette`, `--upstream-timeout` and the five identity and redaction flags are usage errors outside
  `--record`, and so is `--validate-config` together with `--record`.
- `-p` stays required in every mode, `--validate-config` runs included.

**`--validate-config`**
- With `-c`, it works as before.
- With `--replay`, it validates the cassette (§9) and exits without serving.

**`TARGET_BASE_URL`**
- Form: `http(s)://host[:port][/prefix]`.
- Refused at startup: a query, a fragment, or userinfo (`user:pass@`). Userinfo would otherwise be written
  to the cassette in clear.
- A trailing slash is stripped.

**`--upstream-timeout`** is a deadline for the whole exchange (§7), 30 s by default.

**Runtime**
- The server stays single-threaded and binds `0.0.0.0`. In record mode one slow target blocks other
  clients for up to the deadline; this is accepted for v1.
- Ctrl+C and SIGTERM stop the server as in config mode. In record mode the exchange in flight is not
  written, and the cassette on disk is complete as of the last finished interaction.

## 4. Request intake (all modes)

**Raw target and raw headers.** wsgiref hands the application a percent-decoded `PATH_INFO`: `/v1/a%2Fb`
arrives as `/v1/a/b`. It also folds headers, joining repeats with commas and turning `-` into `_`, so
`X-Api-Key` and `X_Api_Key` collide. Forwarding from that would alter requests on their way to the
target. httpmocker's own request handler (`ClientPortRequestHandler`) therefore adds to the environ:
- the raw request target, i.e. path and query as on the request line;
- the raw header list: names as sent, in order, repeats kept.

**What uses which**
- Forwarding uses the raw target and the raw header list.
- Identity uses the decoded path (the string config mode matches on), the parsed query, and
  `match_headers` values taken from the raw list.

**Methods.** GET, POST, PUT, DELETE, PATCH, HEAD and OPTIONS, in every mode. Any other method gets
Bottle's own `405` and is **not journaled**: Bottle answers it before any httpmocker code runs.

**Bodies are read as bytes**, never through Bottle's `request.json`. That parser answers a JSON body over
100 KiB with `413` and a malformed one with `400`, which in 0.2.0 replaced config mode's configured
response; 0.3.0 fixes it in every mode.

**Chunked request bodies** are accepted in every mode. Bottle decodes `Transfer-Encoding: chunked` itself.
In record mode such a body is forwarded with a computed `Content-Length` and recorded de-chunked.

## 5. Transport headers

The list: `Connection` and every header it names, `Keep-Alive`, `Proxy-Connection`, `TE`, `Trailer`,
`Transfer-Encoding`, `Upgrade`, `Content-Length`, `Host`, `Proxy-Authenticate`. wsgiref answers `500`
when an application sets `Proxy-Authenticate`, so a target's `407` would break the exchange if that
header were passed on.

**The rule.** A transport header is never forwarded as received: `Host` is rewritten and `Content-Length`
computed. It is never written to the cassette and never shown in the journal.

**Exception for `HEAD`.** A `HEAD` response keeps its `content-length` in three places:
- on the response forwarded in record mode;
- in the cassette;
- on replay.

Without it, a `HEAD` would answer `Content-Length: 0`, unlike the target. Config mode already behaves this
way.

**Hand-edited cassettes.** A transport header added by hand fails validation. The only exception is
`content-length` on a `HEAD` response.

**Console.** The console's request block keeps showing every request header, `Host` and `Content-Length`
included; only redaction (§11) changes what it prints.

## 6. Record mode

**Forwarding the request**
- Method, and the URL as base URL + raw path, plus `?` + raw query when there is one.
- Every non-transport header as received, in order and with repeats, and the body bytes.
  `Content-Length` is computed from the body. `POST`, `PUT` and `PATCH` send `Content-Length: 0` when they
  have no body; other methods send none.
- `Host` is the target's `host[:port]` as written, a non-default port included.
- `Accept-Encoding: identity` **replaces** the client's value on the wire, so bodies land readable. The
  cassette and the journal keep the client's header as received.
- Redirects are not followed: a `3xx` is returned and recorded like any other response.

**Answering the client**
- The target's status, every non-transport header (plus `content-length` on `HEAD`) and the body bytes. No
  other rewriting.
- The recorder **reads the target's whole response before it answers**.
- A target answering with `Transfer-Encoding: chunked` is read whole: `http.client` decodes the chunks.
  The response is recorded de-chunked and sent to the client with a computed `Content-Length`.
- A response that carries `Content-Encoding` despite `identity` is stored as `body_base64` together with
  that header, and replayed verbatim.

**Upstream failure** (target unreachable, TLS failure, deadline exceeded)
- The client gets `502` with `X-Httpmocker: upstream-error` and the body
  `{"httpmocker": "upstream-error", "cause": "<text>"}`. The cause is one of:
  - `connection refused by <host:port>`;
  - `cannot resolve <host>: …`;
  - `TLS certificate verification failed: …`, or `TLS error: …`;
  - `the target closed the connection without a response`;
  - `invalid response from the target: …`;
  - `no complete response within the deadline of <n> s`;
  - another socket error, by name and text.
- The journal gets an entry with outcome `upstream_error`.
- Nothing is recorded. A session in which the deadline fired is simply recorded again.

**TLS.** The upstream client is `http.client` with `ssl.create_default_context()`. Verification is always
on, and trust follows §2. The standard library is a requirement, not an implementation hint: `requests`
would use its own `certifi` bundle and ignore both environment variables.

**Cassette.** One interaction is appended per completed exchange, after redaction (§11), and written as in
§8. It is on disk before the client receives the response. The console closes the request's block with
`PUT /v1/items → 201 (123 ms) recorded #5`, where the number is the interaction's 0-based index.

**Reserved prefix.** Requests under `/_httpmocker` are never forwarded and never recorded (§13).

## 7. Upstream deadline

- **Span.** `--upstream-timeout SECONDS` runs from the start of the connect to the last byte of the
  response body.
- **How it is kept.** The connect gets the whole deadline as its timeout. Once connected, a watchdog
  shuts the socket down when the deadline passes. A target that keeps sending a byte at a time, in the
  status line, the headers or the body, therefore cannot stretch the exchange. A body that ends with the
  connection and is cut this way counts as a deadline error, never as a short body.
- **Default.** 30 s.
- **Why a total, not per read.** The recorder sends nothing until it has the last byte. A client waiting
  for the first byte therefore measures the whole exchange, even when its own timeout is per read. With a
  per-read deadline, a target that sends a long body slowly would be recorded after the client had
  already given up. The cassette would then hold an exchange the client never saw.
- **Usage rule.** Set the deadline **below** the client's own timeout, with a margin, e.g. 290 s for a
  client that waits 300 s.
- **Limit of the rule.** It holds for a client that sends one request at a time. A request queued behind
  another slow exchange spends its waiting time on the client's clock only.

## 8. Cassette on disk

**Writing**
- `--record` writes the cassette with `"interactions": []` before the server listens. A permission
  problem then stops the start instead of failing the first request.
- After every interaction the whole file is rewritten atomically: a temp file
  (`.<name>.<pid>.tmp`) in the same directory, flush, fsync, then `os.replace`. Cassettes are
  test-sized, so the cost of rewriting does not matter.
- The JSON is indented by two spaces, for hand editing. The file's mode follows the umask, as for any
  file the user creates; it is not a private 0600 temp file.

**Starting on an existing file.** `--record` refuses to start unless the cassette path is one of:
- absent;
- a file of zero bytes;
- a JSON object with `httpmocker_cassette` 1 and an empty `interactions`, as a session stopped before
  its first request leaves it. Replay rejects such a cassette (§9), so it is only good for recording
  over.

There is no silent overwrite and no appending across sessions.

**In a container**
- `os.replace` onto a single-file bind mount fails with `EBUSY`, so mount the cassette's **directory**.
- A file the container writes belongs to its user (uid 999). To edit a cassette by hand on the host, run
  the recording container with `--user "$(id -u):$(id -g)"`.

## 9. Cassette format

```json
{
  "httpmocker_cassette": 1,
  "tool_version": "0.3.0",
  "recorded_at": "2026-10-19T12:00:00Z",
  "target": "https://api.example.test",
  "identity": {"ignore_query_params": ["ts"], "ignore_json_paths": ["/request_id"], "match_headers": []},
  "redacted_headers": ["authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key"],
  "redacted_query_params": [],
  "interactions": [
    {
      "request":  {"method": "PUT", "path": "/v1/items", "query": {"ts": ["1760875200"]},
                   "headers": {"content-type": "application/json", "x-api-key": "<redacted>"},
                   "body": {"name": "sample", "request_id": "a1"}},
      "response": {"status": 201, "headers": {"content-type": "application/json"},
                   "body": {"id": 7, "name": "sample"}},
      "meta":     {"recorded_at": "2026-10-19T12:00:01Z", "duration_ms": 123, "delay_ms": 0}
    }
  ]
}
```

**Required and optional fields.** Required: `httpmocker_cassette`, `interactions`, and in each interaction
`request.method`, `request.path` and `response.status`. Everything else may be left out, which matters for
a hand-written cassette:
- `tool_version`, `recorded_at`, `target`, `meta.recorded_at` and `meta.duration_ms` are absent;
- the `identity` lists, `redacted_query_params`, `query` and `headers` are empty;
- `redacted_headers` holds the four defaults of §11;
- `meta.delay_ms` is `0`.

**Request fields**
- `path` is the decoded path, the identity form.
- `query` is the parsed multimap `{name: [values]}`, with blank values kept. A redacted parameter's
  values are `<redacted>`.

**Headers**
- Names are lower-cased; validation refuses a name with a capital letter, here and in `match_headers` and
  `redacted_headers`.
- A repeated header is a non-empty list of strings; otherwise the value is a string.
- No name or value may hold a CR, LF or NUL character, which no header can carry on the wire.
- A redacted header's value is `<redacted>`.

**Bodies.** Exactly one representation, or none; an empty body has none.
- `body`: parsed JSON. Used when all of these hold:
  - the media type is `application/json` or ends in `+json`, parameters ignored;
  - the bytes decode as UTF-8 and parse;
  - the canonical dump succeeds with `allow_nan=False`. Otherwise a body such as `1e400` would replay as
    `Infinity`, which is not JSON.
- `body_text`: any other body that is valid UTF-8. It is decoded as UTF-8 whatever charset is declared,
  and re-encoded as UTF-8 on replay.
- `body_base64`: anything else.

`"body": null` is a body: a JSON `null`. It counts as a representation, so a message cannot also carry
`body_text` or `body_base64`.

**What replay serves**
- `body` as canonical compact JSON. The bytes may differ from what the target sent, so JSON bodies are
  compared as JSON.
- `body_text` and `body_base64` byte for byte.
- A recorded `Content-Length` is never trusted, except on `HEAD` (§5).

**`meta`**
- `delay_ms` is `0` when recorded and can be edited by hand to simulate latency on replay.
- `duration_ms` is what the target took, for information; replaying it is out of v1.

**Hand editing.** The file is meant to be edited by hand: reorder, delete, add `delay_ms`, add
interactions. Validation catches structural mistakes, not semantic ones. A hand-added request without a
`content-type` keys its `body_text` as raw UTF-8 bytes.

**Validation is strict.** It runs when `--replay` starts and with `--replay FILE --validate-config`. A valid
cassette prints `✓ Cassette file '<file>' is valid` and its number of interactions. Any error exits with
code 1, with pydantic's report naming each error's place (e.g. `interactions.2.meta.delay`). As with a
config, checks that span several fields show once the fields themselves pass:
- the transport headers of an interaction;
- `match_headers` against `redacted_headers`.

It checks:
- unknown fields are errors;
- `httpmocker_cassette` must be `1`. A later format version may refuse version-1 files unless its release
  says otherwise;
- **at least one interaction**, as a config needs at least one endpoint;
- the method is one of the seven;
- the path starts with `/` and is not under `/_httpmocker` (§13). Unlike a config path it may hold `?`
  or `#`: a recorded path is percent-decoded, so a request for `/files/a%3Fb` is recorded as
  `/files/a?b`, and replay matches it in the same decoded form;
- the status is 100–599, as in config mode;
- `delay_ms` is ≥ 0;
- at most one body representation, and `body_base64` must be valid base64;
- no transport headers, except `content-length` on a `HEAD` response;
- `match_headers` is disjoint from `redacted_headers` and from the transport headers;
- JSON Pointers are RFC 6901: non-empty and starting with `/`.

## 10. Identity key and replay

**The identity key** of a request is the tuple:

1. **Method**, upper-cased.
2. **Path**: the decoded path.
3. **Query**: parsed with blank values kept, so `?flag` and `?flag=` both give `flag: [""]`, and `+` and
   `%20` both give a space. It is a multimap sorted by name, then by value, **minus**
   `ignore_query_params` and `redacted_query_params`.
4. **Body**:
   - When the media type is JSON (§9) and the body parses: the canonical JSON **after deleting the
     `ignore_json_paths` pointers**. Canonical means
     `json.dumps(json.loads(b), sort_keys=True, separators=(",", ":"), ensure_ascii=False)`; that
     round-trip *is* the definition, so `1` and `1.0` key differently and `1.10` keys as `1.1`.
   - Pointers apply in the order listed, each to the result of the one before. One that does not resolve
     is a no-op.
   - Otherwise: the raw bytes.
   - For a cassette request, the bytes are first rebuilt from whichever representation it carries, so a
     live request and its recording key identically. A recorded `body` keys as JSON whatever the
     request's headers say: the representation itself says the body is JSON, which helps a hand-added
     interaction without a `content-type`.
   - The media type comes from the live request's `Content-Type`, or from the recorded request's
     `content-type` header (its first value).
5. **Headers**: the values of the headers named in `match_headers` (lower-cased names), taken from the raw
   list and compared exactly. An absent header keys as *absent*, distinct from an empty value. A repeated
   header keys as the list of its values in order. By default the list is empty, so headers never take
   part in matching.

No wildcards, no partial matches, no scoring: two requests are the same iff their keys are equal.

**Sequences**
- Interactions with equal keys form a sequence, in cassette order.
- The n-th request with a key gets the n-th interaction.
- Positions count per key from server start and are reset by `DELETE /_httpmocker/journal`.

**Serving a match**
- The interaction's status, headers and body (§9), exactly. Header names go out as stored, lower-cased.
  A response recorded without a `content-type` goes out without one: Bottle's default
  `Content-Type: text/html` is removed. `Content-Length` is computed from the body, except on `HEAD`
  (§5).
- Response headers listed in the cassette's `redacted_headers` (e.g. `set-cookie`) are not sent at all.
  Their recorded values are placeholders.
- `meta.delay_ms` is applied, interruptibly, as in config mode.

**Miss and exhausted.** A request whose key has no interaction is a **miss**; the request after the last
of its sequence is **exhausted**. Either one gets:
- status `500` with `X-Httpmocker: miss` or `exhausted`;
- the JSON body `{"httpmocker": "miss" | "exhausted", "key": {…}}`;
- a journal entry carrying the key in its `key` field;
- a line that stands out on the console (§15).

The `key` object is `{"method", "path", "query", "body", "headers"}`:
- `body` follows the §9 representation rules, with the JSON one in canonical form, and is omitted when
  there is none;
- `headers` holds only the `match_headers` values, and is omitted when that list is empty.

No status code is unambiguous against a real API, so the **journal is the authoritative signal**: tests
assert `miss + exhausted == 0`. A client that retries on 5xx repeats a miss, and each repeat is another
entry.

**Replay startup.** Replay reads its lists from the cassette and accepts no identity or redaction flags.
At start the console prints the lists it loaded.

## 11. Redaction

**The lists**
- `redacted_headers`: by default `authorization`, `proxy-authorization`, `cookie`, `set-cookie`. These four
  are always included; `--redact-header` adds names. Header names are case-insensitive.
- `redacted_query_params`: empty by default; `--redact-query-param` adds names.

**What is shown (console, journal)** is redacted in every mode:

| Mode | Lists |
|---|---|
| `-c` | the default list |
| record | the default list plus the flags |
| replay | the default list **united with** the cassette's lists |

No redacted value reaches stdout in any mode. For config mode this is a visible change from 0.2.0, which
printed every header in clear. In replay the union keeps a hand-edited cassette harmless: one whose
`redacted_headers` lost `authorization` still does not show it.

**What replay omits** from responses is decided by the cassette's own `redacted_headers` (§10). A
hand-edited cassette can deliberately put a real `set-cookie` back and have it replayed, while the
console and the journal still redact it.

**Placeholders**
- In the cassette and on the console: `<redacted>`.
- In the journal: `<redacted:` + the first 12 hex digits of SHA-256 of the original value's UTF-8 bytes +
  `>`, so a test can assert *which* credential a request carried. Each value of a repeated header is
  hashed on its own.

**Effect on traffic.** A redacted header or query parameter is still forwarded intact to the target.
During recording the client receives the target's real `set-cookie`.

**Effect on matching**
- A redacted query parameter is dropped from the key.
- A header both redacted and in `match_headers` is refused: at startup when recording, at validation for a
  cassette. Consequence, accepted: session cookies cannot take part in matching in v1.

**Bodies are never redacted.** A secret inside a body is written verbatim; don't record such bodies, or
edit the cassette by hand.

**Identity versus redaction.** `--ignore-query-param` removes a parameter from the key only; its value is
still stored. A value that is a secret needs `--redact-query-param`.

## 12. Journal

The journal lives in memory, exists in every mode, and is not persisted.

**`GET /_httpmocker/journal`** answers `200`, `X-Httpmocker: journal`, and:

```json
{"counts": {"served": 0, "recorded": 0, "miss": 0, "exhausted": 0,
            "upstream_error": 0, "rejected": 0, "unmatched": 0},
 "dropped": 0,
 "entries": []}
```

**Counts and the cap**
- Counts are running totals since the last reset; they are not derived from the entries.
- Only the last 10 000 entries are kept. `dropped` is the number of entries evicted since the reset.

**`DELETE /_httpmocker/journal`** answers `204` and zeroes:
- the entries;
- the counts;
- `dropped`;
- all sequence positions in replay, i.e. "start the next scenario".

**Other methods**
- Requests to the journal are not journaled themselves, nor shown on the console: a test that polls the
  journal would otherwise bury the requests it is checking.
- Only `GET` and `DELETE` are served on the journal path. Any other method, `HEAD` included, gets `405`
  with the marker, `Allow: GET, DELETE` and the body
  `{"httpmocker": "journal", "error": "method not allowed"}`.

**Entries.** Exactly one per exchange, with exactly one outcome:

| Mode | Outcomes |
|---|---|
| `-c` | `served`, `unmatched` (the `404` for no endpoint), `rejected` |
| record | `recorded`, `upstream_error`, `rejected` |
| replay | `served`, `miss`, `exhausted`, `rejected` |

`rejected` is a request under the reserved prefix other than the journal (§13). In record mode a forwarded
exchange is `recorded`, never also `served`.

**Entry fields**
- `seq` counts from 1 after each reset, so the first entry kept has `seq` = `dropped` + 1.
- `received_at`: UTC, ISO 8601 with milliseconds, e.g. `2026-10-21T12:00:00.123Z`.
- `request`:
  - `method`, `path` (decoded), `query` (the multimap of §9, names in order of first appearance);
  - `headers` in the form of §9 (lower-cased names, a repeated header as a list), without transport
    headers and redacted in the journal form;
  - `body` per the §9 representation rules;
  - `body_size`, the bytes received, on every entry.
- **Body limit.** A body over **64 KiB** is not stored; the entry carries `"body_omitted": true` instead.
  It is omitted rather than cut, because a cut JSON body would not parse. The cassette always holds full
  bodies.
- `outcome` and `status`, the status answered.
- `interaction_index` in replay, when an interaction was served: the 0-based index into the cassette's
  `interactions`. The console shows the same number.
- `key` in replay, on a `miss` or `exhausted` entry: the request key as the answer's body shows it (§10).

**Exposure.** The journal is unauthenticated on every interface the server binds. Its hashes let anyone
who can reach it confirm guesses of weak secrets, such as Basic-auth passwords. That is acceptable for a
test tool, and the README says so.

## 13. Reserved prefix `/_httpmocker`

- The reserved paths are `/_httpmocker` itself and everything starting with `/_httpmocker/`.
  `/_httpmockerX` is not reserved.
- **Config files.** An endpoint path under the prefix fails `-c` validation. This is the only change to
  the config format.
- **Cassettes.** An interaction path under the prefix fails cassette validation.
- **At runtime, every mode.** Any request under the prefix other than the journal gets `404`,
  `X-Httpmocker: reserved`, the body `{"httpmocker": "reserved"}` and the journal outcome `rejected`. It
  is never forwarded and never recorded.

## 14. Responses generated by the tool

Every response the tool generates itself carries the marker `X-Httpmocker: <kind>`, so it can never be
mistaken for a recorded or forwarded one:

| Kind | Status | When |
|---|---|---|
| `miss` | 500 | replay, no interaction for the key |
| `exhausted` | 500 | replay, sequence used up |
| `upstream-error` | 502 | record, target unreachable, TLS failure or deadline exceeded |
| `reserved` | 404 | any mode, a path under the prefix other than the journal |
| `journal` | 200 / 204 / 405 | the journal endpoint |

Config mode's own `404 {"error": "endpoint not found"}` stays exactly as in 0.2.0, without a marker. The
journal outcome `unmatched` already identifies those requests.

## 15. Console

- **The block.** Each request gets the multi-line block as in config mode: request line, headers (redacted,
  §11), pretty-printed body. Record and replay close it with a one-line verdict:
  - record: `PUT /v1/items → 201 (123 ms) recorded #5`; a failed exchange, in white on red:
    `UPSTREAM ERROR: PUT /v1/items → 502, <cause>; nothing recorded`;
  - replay: `PUT /v1/items → 201 served #5 (2/3)`: interaction 5 of the cassette, the second of the
    three recorded for this request;
  - miss or exhausted, in white on red: `MISS: PUT /v1/b → 500, no recorded interaction for this
    request` or `EXHAUSTED: PUT /v1/a → 500, all 2 recorded response(s) for this request were used`.
- **Startup.**
  - Record prints the target, the cassette, the deadline and the lists.
  - Replay prints the cassette and the lists it loaded.

## 16. Out of scope (v1)

- **Replay and cassettes.** Fallback to the target, appending to a cassette across sessions, merging
  cassettes.
- **Dynamic behaviour.** Response templating, value echo, injection, named states, rich matchers (glob,
  regex, JSONPath), scoring, wildcards in ignore paths.
- **Faults and redaction.** Faults beyond `delay_ms` (hang, dropped connection), replaying recorded
  latency, redaction inside bodies.
- **Responses the tool sends.** The tool never *sends* a streamed or chunked response. A target's chunked
  response is still recorded and replayed (§6).
- **Transport and deployment.** WebSocket, HTTPS on the listening side, forward-proxy (CONNECT) capture,
  an egress proxy, `--ca-file` / `--insecure`, persisting the journal, a UI, methods beyond the seven.

## 17. Example cassette and smoke tests

As config mode has `configs/example.json` and `smoke-tests.md`, replay gets the same:

- **Example.** A hand-written `cassettes/example.json` holds seven interactions:
  - `PUT /api/items/sample` answering `201`, then `409`;
  - `GET /api/items` with the item, after a hand-added `delay_ms` of 250;
  - `DELETE` twice, both `204`;
  - `GET /api/items` empty;
  - `GET /api/health` with a `body_text`.

  It ignores `ts` and `/request_id` for identity and redacts `x-api-key`. A test keeps it valid.
  `.gitignore` keeps only the shipped example in `cassettes/`, as it does for `configs/` and `payloads/`,
  and `.dockerignore` keeps `cassettes/` out of the image.
- **`smoke-tests.md`** gains a section with curl calls that replay the example cassette and query and
  reset the journal.
- **`scripts/smoke/replay-example.sh <image>`** replays the example cassette in a Docker image and
  checks the answers (201, 409, exhausted, miss, `ok`), the journal counts and a reset. CI and the
  release workflow both run it, so the release step runs the same code CI has already run.
- **CI.** The CI workflow's image job already builds the image for the Trivy scan. It also runs the
  script on that image, so a break shows on a push or a pull request.
- **`release.yml`.** Before pushing the image, the release workflow runs the same script against the
  image it is about to publish. That step runs only on a tag, so its own first run is the 0.3.0
  release, but the script has run in CI on every push before.

## 18. Tests in the repository

**Dependency-free, as before**
- The WSGI app is driven in-process.
- A synthetic stateful upstream runs on stdlib `http.server` in a thread:
  - `PUT /v1/items {name}` answers `201` the first time and `409` with a JSON body on a repeat;
  - `GET /v1/items` answers the list;
  - `DELETE /v1/items/{name}` answers `204` and is idempotent.

**TLS fixtures**
- A CA and a server certificate are generated per test session into a temporary directory with the
  `openssl` CLI, and never written into the repository.
- Their extensions make them valid under `VERIFY_X509_STRICT`, which `ssl.create_default_context()` sets
  since Python 3.13:
  - CA: critical `basicConstraints` and `keyUsage`, subject key identifier;
  - server: SAN `DNS:localhost, IP:127.0.0.1`, authority key identifier, `extendedKeyUsage` `serverAuth`.
- When `openssl` is missing and the environment variable `CI` is set, the TLS tests **fail**; otherwise
  they are skipped with a reason. GitHub Actions sets `CI` in every job, so the CI workflow and the
  release workflow's test run cannot pass with the TLS tests silently skipped.

**Standards.** Coverage stays at 85 % or more; ruff and strict mypy stay clean. Every 0.2.0 test stays
unchanged and green.

## 19. Acceptance scenarios

These run against the published image and wheel. The repository's own suite covers the same ground.

1. **Round trip.** `PUT a, PUT a, GET, DELETE a, DELETE a, GET` through `--record` gives a cassette with 6
   interactions in order. The same scenario against `--replay` yields the identical sequence of statuses
   and bodies, with JSON compared as JSON. The journal shows `served: 6, miss: 0, exhausted: 0`.
2. **Exhausted.** One extra `PUT a` gets the marker response; the journal shows `exhausted: 1` with the
   key.
3. **Miss.** `PUT b` gets the marker response; the journal shows `miss: 1`.
4. **Identity lists.** Recorded with `--ignore-query-param ts --ignore-json-path /request_id` and replayed
   with other `ts` and `request_id` values: `miss: 0`. The same scenario recorded without the lists:
   `miss: 1`.
5. **Headers out of the key.** A different `User-Agent` matches. With `--match-header user-agent` it does
   not.
6. **Redaction.** With `--redact-header x-api-key`:
   - the cassette and the console show `<redacted>`;
   - the journal shows `<redacted:` + 12 hex of SHA-256 + `>`, matching the value sent;
   - the upstream received the real value.

   The same holds for `--redact-query-param api_key`, which then no longer takes part in matching.
7. **Reset.** `DELETE /_httpmocker/journal` zeroes the counts, and the scenario replays green a second
   time. Without the reset, the first request is exhausted.
8. **TLS.** Against an HTTPS upstream with a private-CA certificate:
   - untrusted CA: `502` and `upstream_error: 1`;
   - `SSL_CERT_FILE=ca.pem`: success;
   - verification is never off.
9. **Refusals and intake.** Each of these is refused:
   - `-c` together with `--replay`: usage error;
   - `--record` on an existing non-empty cassette;
   - `--redact-header x` together with `--match-header x`;
   - a cassette path under `/_httpmocker`: validation error.

   And a chunked request body in record mode is forwarded with a computed `Content-Length` and recorded
   de-chunked.
10. **Back-compat.** Every 0.2.0 test stays unchanged and green. Config mode is unchanged except for:
    - the journal;
    - the reserved prefix;
    - console redaction;
    - the fix of JSON request bodies that got `413` or `400` instead of the configured response (§4).

## 20. Documentation and release

**README "Record and replay"**, derived from this document:
- both TLS deployments:
  - a CLI host that trusts the CA;
  - a container with `./ca.pem` mounted read-only and `SSL_CERT_FILE` pointing at it. `SSL_CERT_FILE`
    replaces the default roots, so that file must hold every CA the run needs;
- a compose example with the CA mount;
- for recording in a container: the directory mount and `--user`;
- the trust-store check `python -c "import ssl; print(ssl.get_default_verify_paths())"`. A uv-managed
  CPython looks at `/etc/ssl/{cert.pem,certs}`, and macOS python.org builds trust nothing until
  `Install Certificates.command` has run;
- the deadline as a total, with the margin rule;
- the limitations:
  - redirects and absolute URLs point at the real system;
  - `Content-Encoding` is replayed verbatim, and JSON bodies are replayed canonical;
  - secrets in bodies are not redacted;
  - no egress proxy, and recording is single-threaded;
  - methods outside the seven get `405` and are not journaled;
  - the cassette format version;
  - the journal's cap, body limit and exposure.

**Also:** CHANGELOG entries, the CLAUDE.md architecture, and `smoke-tests.md` (§17).

**Release.** 0.3.0 by the repository's routine: the CHANGELOG records the digest and the image Id, and the
release files ship with `SHA256SUMS`.

**Build order.** On `main`, each step committed and green on CI before the next:
1. request handler (raw target and headers) and CLI modes;
2. journal and reserved prefix, in every mode;
3. cassette model and validation;
4. replay;
5. record: forwarding, TLS, redaction, atomic writes, deadline;
6. docs and examples;
7. release.
