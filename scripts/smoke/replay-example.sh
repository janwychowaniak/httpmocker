#!/usr/bin/env bash
# Replay cassettes/example.json in an image and check what it answers and journals.
#
#   replay-example.sh <image> [container name] [host port]
#
# Run from the repository root: the cassettes/ directory is mounted read-only into the
# container. CI runs it on the image it scans, the release workflow on the image it is
# about to push, so a break in replay or the journal shows before the tag does.
set -euo pipefail

image=$1
name=${2:-httpmocker-replay-smoke}
port=${3:-8080}
base="http://127.0.0.1:$port"

trap 'docker logs "$name" 2>&1 | tail -n 40; docker rm -f "$name" > /dev/null 2>&1 || true' EXIT

docker run -d --name "$name" -p "127.0.0.1:$port:8080" \
    -v "$PWD/cassettes:/app/cassettes:ro" \
    "$image" httpmocker -p 8080 --replay cassettes/example.json > /dev/null
for _ in $(seq 1 30); do
    curl -fsS "$base/_httpmocker/journal" > /dev/null 2>&1 && break
    sleep 1
done

check() {
    if [[ "$2" != "$3" ]]; then
        echo "::error::$1: expected $3, got $2"
        exit 1
    fi
    echo "ok: $1: $2"
}
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
# ts and request_id differ on every call: the example leaves both out of request identity
put() {
    status -X PUT "$base/api/items/sample?ts=$1" -H 'Content-Type: application/json' \
        -d "{\"name\": \"sample\", \"request_id\": \"$1\"}"
}
counts() {
    curl -fsS "$base/_httpmocker/journal" | python3 -c \
        'import json, sys; c = json.load(sys.stdin)["counts"]; print(c["served"], c["exhausted"], c["miss"])'
}

check "first PUT" "$(put 1)" 201
check "second PUT" "$(put 2)" 409
check "third PUT, exhausted" "$(put 3)" 500
check "unrecorded request, miss" "$(status "$base/api/nothing")" 500
check "GET /api/health" "$(curl -fsS "$base/api/health")" ok
check "journal served, exhausted, miss" "$(counts)" "3 1 1"
check "journal reset" "$(status -X DELETE "$base/_httpmocker/journal")" 204
check "first PUT after the reset" "$(put 4)" 201
