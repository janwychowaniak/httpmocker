```sh
curl -X GET http://localhost:8080/api/users | jq
curl -X POST http://localhost:8080/api/login | jq
curl -X GET http://localhost:8080/health | jq
curl -X GET http://localhost:8080/api/slow | jq
curl -X PUT http://localhost:8080/api/users/123 | jq
curl -X GET http://localhost:8080/api/some-urls | jq

curl -X DELETE -i http://localhost:8080/api/sessions
curl -I http://localhost:8080/api/users
```
