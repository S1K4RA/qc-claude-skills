#!/usr/bin/env python3
"""A deliberately buggy local API for testing probe.py and serve.sh.

  python3 mock_api.py <port>

Each route has one planted defect the engine should flag:
  GET /api/items?page=<int, required>   500 + Python traceback on a non-integer page
  GET /api/items/{id}                    500 on ids past 2^31, 404 on non-digits
  GET /api/search?q=<str>                no auth, reflects q unescaped
  GET /api/secure                        correct: 401 without "Bearer good"
OpenAPI 3 at /openapi.json (routes relative to server url /api).
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "mock", "version": "1"},
    "servers": [{"url": "/api"}],
    "paths": {
        "/items": {"get": {"parameters": [
            {"name": "page", "in": "query", "required": True, "schema": {"type": "integer"}}]}},
        "/items/{id}": {
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
            "get": {}},
        "/search": {"get": {"parameters": [
            {"name": "q", "in": "query", "schema": {"type": "string"}}]}},
        "/secure": {"get": {}},
    },
}


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
                return self.send(500, "Traceback (most recent call last):\n  File \"app.py\"\nValueError")
            return self.send(200, "[]")
        if u.path.startswith("/api/items/"):
            raw = u.path.rsplit("/", 1)[1]
            if not raw.isdigit():
                return self.send(404, '{"error":"not found"}')
            if int(raw) > 2**31:
                return self.send(500, '{"error":"overflow"}')
            return self.send(200, '{"id":%s}' % raw)
        if u.path == "/api/search":
            return self.send(200, "<p>results for %s</p>" % q.get("q", [""])[0], "text/html")
        if u.path == "/api/secure":
            if self.headers.get("Authorization") != "Bearer good":
                return self.send(401, '{"error":"unauthorized"}')
            return self.send(200, '{"ok":true}')
        return self.send(404, '{"error":"not found"}')

    def do_POST(self):
        self.send(405, '{"error":"method not allowed"}')

    do_DELETE = do_POST


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
