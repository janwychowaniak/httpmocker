# Hop-by-hop and framing headers (docs/record-replay.md §5): never forwarded as
# received, never written to a cassette, never shown in the journal. A message's
# Connection header can name further ones. wsgiref answers 500 when an
# application sets Proxy-Authenticate, so it is on the list too.
TRANSPORT_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "host",
        "proxy-authenticate",
    }
)

# Always redacted (docs/record-replay.md §11); --redact-header adds to them.
DEFAULT_REDACTED_HEADERS = ("authorization", "proxy-authorization", "cookie", "set-cookie")
