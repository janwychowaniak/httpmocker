## Config mode

Start the server with `python -m httpmocker -p 8080 -c configs/example.json`, then:

```sh
curl -X GET http://localhost:8080/api/users | jq
curl -X POST http://localhost:8080/api/login | jq
curl -X GET http://localhost:8080/health | jq
curl -X GET http://localhost:8080/api/slow | jq
curl -X PUT http://localhost:8080/api/users/123 | jq
curl -X GET http://localhost:8080/api/some-urls | jq

curl -X DELETE -i http://localhost:8080/api/sessions
curl -I http://localhost:8080/api/users

curl http://localhost:8080/_httpmocker/journal | jq '.counts'
```

## Replay

Start the server with `python -m httpmocker -p 8080 --replay cassettes/example.json`, then
play the example's scenario. `ts` and `request_id` may change on every call: the cassette
leaves both out of request identity.

```sh
put() { curl -s -X PUT "http://localhost:8080/api/items/sample?ts=$RANDOM" \
  -H 'Content-Type: application/json' -H 'X-Api-Key: k-1' \
  -d "{\"name\": \"sample\", \"request_id\": \"$RANDOM\"}" -w ' [%{http_code}]\n'; }

put                                                            # 201
put                                                            # 409
curl -s http://localhost:8080/api/items -w ' [%{http_code}]\n'  # 200 after 250 ms
curl -s -X DELETE -i http://localhost:8080/api/items/sample     # 204, no Content-Type
curl -s -X DELETE -i http://localhost:8080/api/items/sample     # 204
curl -s http://localhost:8080/api/items -w ' [%{http_code}]\n'  # 200, []
curl -s -i http://localhost:8080/api/health                     # 200, text/plain "ok"

put                                                            # 500, X-Httpmocker: exhausted
curl -s -i -X PUT http://localhost:8080/api/items/other         # 500, X-Httpmocker: miss

curl -s http://localhost:8080/_httpmocker/journal | jq '.counts'   # served 7, exhausted 1, miss 1
curl -s -X DELETE http://localhost:8080/_httpmocker/journal       # rewinds every sequence
put                                                            # 201 again
```

`scripts/smoke/replay-example.sh <image>` runs a shorter version of this against a Docker
image; CI runs it on every push.
