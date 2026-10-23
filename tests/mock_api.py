#!/usr/bin/env python3
"""A deliberately buggy local API for testing probe.py, summarize.py and serve.sh.

  python3 mock_api.py <port>

Tokens: "Bearer good" is user A, "Bearer other" is user B. Planted defects:
  GET  /api/items?page=<int>   spec says protected, serves anyone; 500 +
                               Python traceback on a non-integer page
  GET  /api/items/{id}         500 past the spec's maximum id (2^31-1)
  GET  /api/search?q=          public by spec (fine); reflects q into HTML
  GET  /api/lookup?code=       public; echoes code inside a JSON error (a note)
  GET  /api/secure             correct: 401 without a valid token
  POST /api/orders             500 + traceback when "name" is missing; accepts
                               qty above the schema's maximum of 100
  GET  /api/me/orders          same list for every user (not scoped)
  GET  /api/orders/{id}        order 7 is user A's, but user B can read it
  GET  /api/slow?wait=         hangs 2.5 s when wait=0
OpenAPI 3 at /openapi.json (routes relative to server url /api; $refs used
for a parameter and the order schema; global bearer security).
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "mock", "version": "1"},
    "servers": [{"url": "/api"}],
    "security": [{"bearer": []}],
    "components": {
        "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
        "parameters": {"Page": {"name": "page", "in": "query", "required": True,
                                "schema": {"type": "integer", "minimum": 1}}},
        "schemas": {"Order": {"type": "object", "required": ["name", "qty"], "properties": {
            "name": {"type": "string", "maxLength": 10},
            "qty": {"type": "integer", "minimum": 1, "maximum": 100}}}},
    },
    "paths": {
        "/items": {"get": {"parameters": [{"$ref": "#/components/parameters/Page"}]}},
        "/items/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True,
                            "schema": {"type": "integer", "maximum": 2147483647}}],
            "get": {}},
        "/search": {"get": {"security": [], "parameters": [
            {"name": "q", "in": "query", "schema": {"type": "string"}}]}},
        "/lookup": {"get": {"security": [], "parameters": [
            {"name": "code", "in": "query", "schema": {"type": "string", "enum": ["A", "B"]}}]}},
        "/secure": {"get": {}},
        "/orders": {"post": {"requestBody": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Order"}}}}}},
        "/me/orders": {"get": {}},
        "/orders/{id}": {"get": {"parameters": [
            {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}]}},
        "/slow": {"get": {"security": [], "parameters": [
            {"name": "wait", "in": "query", "schema": {"type": "integer"}}]}},
    },
}
TRACE = "Traceback (most recent call last):\n  File \"app.py\", line 3\nKeyError"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, status, body, ctype="application/json"):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def user(self):
        return {"Bearer good": "a", "Bearer other": "b"}.get(self.headers.get("Authorization"))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query, keep_blank_values=True)
        if u.path == "/openapi.json":
            return self.send(200, json.dumps(SPEC))
        if u.path == "/api/items":
            if "page" not in q:
                return self.send(400, '{"error":"page is required"}')
            try:
                int(q["page"][0])
            except ValueError:
                return self.send(500, TRACE, "text/plain")
            return self.send(200, "[]")
        if u.path.startswith("/api/items/"):
            raw = u.path.rsplit("/", 1)[1]
            if not raw.isdigit():
                return self.send(404, '{"error":"not found"}')
            if int(raw) > 2**31 - 1:
                return self.send(500, '{"error":"overflow"}')
            return self.send(200, '{"id":%s}' % raw)
        if u.path == "/api/search":
            return self.send(200, "<p>results for %s</p>" % q.get("q", [""])[0], "text/html")
        if u.path == "/api/lookup":
            code = q.get("code", [""])[0]
            if code in ("A", "B"):
                return self.send(200, '{"code":"%s"}' % code)
            return self.send(400, json.dumps({"error": "unknown code " + code}))
        if u.path == "/api/slow":
            if q.get("wait", [""])[0] == "0":
                time.sleep(2.5)
            return self.send(200, "{}")
        if not self.user():
            return self.send(401, '{"error":"unauthorized"}')
        if u.path == "/api/secure":
            return self.send(200, '{"ok":true}')
        if u.path == "/api/me/orders":
            return self.send(200, '[{"id":7,"owner":"a"}]')
        if u.path.startswith("/api/orders/"):
            raw = u.path.rsplit("/", 1)[1]
            if raw == "7":
                return self.send(200, '{"id":7,"owner":"a"}')
            return self.send(404, '{"error":"not found"}')
        return self.send(404, '{"error":"not found"}')

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/orders":
            return self.send(405, '{"error":"method not allowed"}')
        if not self.user():
            return self.send(401, '{"error":"unauthorized"}')
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            body = json.loads(raw)
        except ValueError:
            return self.send(400, '{"error":"malformed json"}')
        if not isinstance(body, dict):
            return self.send(400, '{"error":"object expected"}')
        name = body["name"] if "name" in body else None
        if name is None:
            return self.send(500, TRACE, "text/plain")
        if not isinstance(name, str) or len(name) > 10:
            return self.send(400, '{"error":"bad name"}')
        qty = body.get("qty")
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            return self.send(400, '{"error":"bad qty"}')
        return self.send(201, '{"id":8}')

    do_DELETE = do_POST


class Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # clients that time out on /api/slow hang up mid-response; that's the point


if __name__ == "__main__":
    Server(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
