#!/usr/bin/env python3
"""Black-box HTTP probe engine for QC of an API you own.

Stdlib only (urllib) so it runs on any Python 3.8+ with no pip installs.

It reads a target's OpenAPI document (OpenAPI 3 or Swagger 2, JSON; YAML too
when PyYAML happens to be installed) or an explicit endpoint list, then sends a
bounded set of *non-destructive* probes per mode and records what came back.
It does not decide pass/fail on its own for the subtle cases — it emits
structured JSON that the calling skill (Claude) reads and judges against the
endpoint's intended behaviour. Clear-cut signals are flagged inline so nothing
gets lost; summarize.py condenses a run (or compares it with an earlier one).

Modes:
  negative  wrong types, missing required fields, bad methods, malformed bodies
  edge      boundary values, from the schema (min/max, maxLength, enum, format)
            plus generic ones: empty, huge, zero, negative, unicode, whitespace
  fuzz      random/structured junk in each parameter and body field
  security  authz gaps (no/invalid/forged token), injection *markers* that
            should be rejected or sanitised, security headers, info leakage
  scope     the same requests as user A and as user B (needs --token-env-b):
            does B see A's resources, or identical data where it should differ

Flags (heuristics for the caller to judge):
  server-error       5xx
  exception-leak     stack trace, SQL or driver error in the body
  reflected-marker   an injection marker came back in a non-JSON body
  auth-not-enforced  2xx without a valid token on a route the spec protects
  accepted-invalid   2xx on input the schema or the probe says is invalid
  timeout            no answer within --timeout (a hang)
  no-response        connection refused/reset mid-run (a crash?)
  slow               answered, but slower than --slow-ms
  cross-user-access  scope: user B got a 2xx on a path filled with A's ids
  same-data-both-users  scope: identical 2xx bodies (16+ bytes) for A and B;
                     expected on shared reference data, so judge or accept it

Scope & safety (enforced here, not just documented):
  - Only http(s)://localhost, 127.0.0.1, or a host passed via --allow-host.
    Any other host aborts. This keeps probes on a service you started or an
    environment you explicitly authorised.
  - Read-only by default. State-changing methods (POST/PUT/PATCH/DELETE) are
    skipped in every mode unless --allow-writes is given. Even then the bodies
    are invalid variants meant to be *rejected*; one that is accepted is a
    finding and also a write, so use it only against a throwaway database.
  - A global request cap, a per-request timeout and an optional delay keep this
    from ever looking like a load or denial-of-service test.

Endpoint list format (--endpoints), when there is no OpenAPI document:
  [{"path": "/api/items/{id}", "method": "GET", "auth": "required",
    "params": [{"name": "page", "in": "query", "type": "integer",
                "required": false, "schema": {"type": "integer", "minimum": 1}}],
    "body_schema": null}]
  "auth" is required | public | unknown; "body_schema" is a JSON Schema object.

Accepted findings (--accept FILE): a JSON list of rules; a flag matching a rule
moves to "accepted_flags" and no longer counts (summarize.py honours the same
file, including "missing-header:<Name>" rules for security headers):
  [{"flag": "auth-not-enforced", "method": "GET", "path": "/api/health",
    "probe": "authz:", "reason": "public by design"}]
  Every key but "flag" is optional; "probe" matches as a prefix.

Usage:
  probe.py --base http://localhost:5040 --mode negative --out results.json \
           [--openapi <url-or-file>] [--endpoints endpoints.json] \
           [--token-env QC_TOKEN] [--token-env-b QC_TOKEN_B] \
           [--auth-header Authorization] [--auth-scheme Bearer] \
           [--value id=7 --value dealer=D01] [--only /api/items] \
           [--allow-writes] [--max-requests 300] [--iterations 25] \
           [--seed 1337] [--timeout 8] [--slow-ms 3000] [--delay-ms 0] \
           [--accept accepted.json] [--allow-host dev.internal] [--fail-on-flags]
"""
import argparse
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ── marker payloads ─────────────────────────────────────────────────────────
# These are detection markers, not weaponised exploits. The point of sending
# them is to confirm the API *rejects or sanitises* them. A well-behaved
# endpoint returns 400/422 or escapes the value; we only flag when the raw
# marker is reflected or the server 5xx-es, which indicates missing validation.
SQLI_MARKERS = ["' OR '1'='1", "1; DROP", "admin'--", "\" OR \"\"=\""]
XSS_MARKERS = ["<script>qcmark</script>", "\"><img src=x>", "javascript:qcmark"]
PATH_MARKERS = ["../../qcmark", "..\\..\\qcmark", "%2e%2e%2fqcmark"]
OVERFLOW = "A" * 20000
UNICODE = "héllo→世界→🙂→\u0000​"
WHITESPACE = "   \t\n  "

EDGE_STRINGS = ["", " ", WHITESPACE, "0", "-1", "-0", OVERFLOW, UNICODE,
                "null", "NaN", "undefined", "true", "999999999999999999999",
                "2020-13-45", "1e309"]
EDGE_NUMBERS = [0, -1, -0, 2**31, -(2**31) - 1, 2**63, -(2**63) - 1,
                999999999999, 0.0000001, -999999999999, float("inf")]

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SECURITY_HEADERS = ("Strict-Transport-Security", "X-Content-Type-Options",
                    "X-Frame-Options", "Content-Security-Policy", "Cache-Control")

# Where frameworks usually serve their OpenAPI document, tried in order when
# --openapi is not given (ASP.NET, FastAPI, Spring, Express/Nest, misc).
OPENAPI_PATHS = ["/swagger/v1/swagger.json", "/openapi.json", "/v3/api-docs",
                 "/swagger.json", "/api-docs", "/api/openapi.json",
                 "/docs/openapi.json", "/api-docs.json", "/openapi.yaml"]

# Lower-cased substrings that mean a response leaked internals: stack traces
# and SQL/driver errors across the common server stacks.
LEAK_SIGNATURES = (
    "stack trace", "stacktrace", "unhandled exception",
    "microsoft.data.sqlclient", "sqlexception", "nullreferenceexception",
    "dbupdateexception", "java.lang.", "exception in thread", "org.hibernate",
    "traceback (most recent call last)", "psycopg2", "sqlalchemy.exc",
    "sqlite3.operationalerror", "django.db", "unhandledpromiserejection",
    "pdoexception", "activerecord::", "sqlstate", "pg::",
    "syntax error at or near", "you have an error in your sql syntax",
    "unclosed quotation mark",
)
# Stack-frame shapes: .NET "   at A.B.C() in /x.cs:line 3", Java "\tat a.b.C(",
# Node "    at Object.<anonymous> (/x.js", Go "goroutine 7 [running]",
# PHP "in /x.php on line 12", Oracle "ORA-00942".
LEAK_PATTERNS = re.compile(
    r"(^|\n)[ \t]+at [\w.$<>`\[\]]+(\.[\w$<>`\[\]]+)*\s*\(|"
    r"goroutine \d+ \[|"
    r"in \S+\.php on line \d+|"
    r"\bora-\d{5}\b")

TIMEOUT = 8.0
SLOW_MS = 3000
ACCEPT_RULES = []


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def host_allowed(base, allow_hosts):
    host = urllib.parse.urlparse(base).hostname or ""
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if host in allow_hosts:
        return True
    # Resolve to check for loopback aliases.
    try:
        if socket.gethostbyname(host).startswith("127."):
            return True
    except OSError:
        pass
    return False


class Caps:
    """Global request budget (and optional spacing) so a run can never turn
    into a load test."""
    def __init__(self, limit, delay_ms=0):
        self.limit = limit
        self.delay = delay_ms / 1000.0
        self.used = 0

    def take(self):
        if self.used >= self.limit:
            return False
        if self.used and self.delay:
            time.sleep(self.delay)
        self.used += 1
        return True


def do_request(method, url, body=None, headers=None, timeout=None, raw=None, limit=8192):
    """`body` is JSON-encoded; `raw` sends bytes/str as-is (for malformed bodies).
    `limit` caps the bytes read from the response; None reads it all (OpenAPI)."""
    headers = dict(headers or {})
    data = None
    if raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        headers.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # local dev certs are self-signed
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT, context=ctx) as r:
            raw = r.read(limit) if limit else r.read()
            return {"url": url[:2000], "status": r.status, "ms": round((time.time() - t0) * 1000),
                    "headers": {k: v for k, v in r.headers.items()},
                    "body": raw.decode("utf-8", "replace")}
    except urllib.error.HTTPError as e:
        raw = (e.read(limit) if limit else e.read()) if hasattr(e, "read") else b""
        return {"url": url[:2000], "status": e.code, "ms": round((time.time() - t0) * 1000),
                "headers": {k: v for k, v in (e.headers or {}).items()},
                "body": raw.decode("utf-8", "replace")}
    except (urllib.error.URLError, socket.timeout, TimeoutError,
            ConnectionError) as e:
        return {"url": url[:2000], "status": None, "ms": round((time.time() - t0) * 1000),
                "headers": {}, "body": "", "error": str(getattr(e, "reason", e))}


# ── OpenAPI loading ─────────────────────────────────────────────────────────

def parse_spec(text):
    try:
        return json.loads(text)
    except ValueError:
        try:
            import yaml  # optional; only when the project already has PyYAML
        except ImportError:
            return None
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            return None


def fetch_spec(args):
    """Return (spec dict, source) from --openapi (URL or file) or discovery."""
    if args.openapi and not args.openapi.startswith(("http://", "https://")):
        with open(args.openapi, encoding="utf-8") as f:
            return parse_spec(f.read()), args.openapi
    candidates = [args.openapi] if args.openapi else [
        args.base.rstrip("/") + p for p in OPENAPI_PATHS]
    for url in candidates:
        r = do_request("GET", url, timeout=10, limit=None)
        if r["status"] == 200 and r["body"]:
            spec = parse_spec(r["body"])
            if isinstance(spec, dict) and spec.get("paths"):
                return spec, url
    return None, ", ".join(candidates)


def resolve(spec, node, depth=0):
    """Inline local $refs (and merge allOf) so schemas carry their real limits.
    Depth-capped, so recursive schemas end as {} instead of looping."""
    if depth > 8:
        return {}
    if isinstance(node, list):
        return [resolve(spec, n, depth + 1) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) \
                if isinstance(target, dict) else {}
        return resolve(spec, target, depth + 1)
    out = {k: resolve(spec, v, depth + 1) for k, v in node.items()}
    if "allOf" in out:
        merged = {"type": "object", "properties": {}, "required": []}
        for part in out.pop("allOf"):
            merged["properties"].update(part.get("properties", {}))
            merged["required"] += part.get("required", [])
        merged["properties"].update(out.get("properties", {}))
        merged["required"] += out.get("required", [])
        out = {**out, **merged}
    for key in ("oneOf", "anyOf"):
        if key in out and out[key] and "type" not in out:
            out = {**out[key][0], **{k: v for k, v in out.items() if k != key}}
    return out


def auth_of(spec, op):
    """required | public | unknown, from the operation's or the global
    `security`. An empty list, or an empty requirement, means public."""
    sec = op.get("security", spec.get("security"))
    if sec is None:
        return "unknown"
    if not sec or any(not req for req in sec):
        return "public"
    return "required"


def load_endpoints(args):
    """Return [{path, method, params, has_body, body_schema, auth}]."""
    if args.endpoints:
        with open(args.endpoints, encoding="utf-8") as f:
            eps = json.load(f)
        for ep in eps:
            ep.setdefault("auth", "unknown")
            ep.setdefault("body_schema", None)
            ep.setdefault("has_body", bool(ep["body_schema"]))
            for p in ep.get("params", []):
                p.setdefault("schema", {"type": p.get("type", "string")})
        return eps
    spec, source = fetch_spec(args)
    if not spec:
        print(f"could not load an OpenAPI document from {source}; pass "
              "--openapi <url-or-file> or --endpoints <file>", file=sys.stderr)
        return []
    # Path prefix the routes are mounted under: Swagger 2 basePath, or the
    # path part of the first OpenAPI 3 server url.
    prefix = spec.get("basePath") or ""
    servers = spec.get("servers") or []
    if not prefix and servers and isinstance(servers[0], dict):
        prefix = urllib.parse.urlparse(servers[0].get("url", "")).path
    prefix = prefix.rstrip("/")
    eps = []
    for path, item in spec.get("paths", {}).items():
        item = resolve(spec, item, 0) if "$ref" in item else item
        shared = item.get("parameters", [])  # path-level parameters
        for method, op in item.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                continue
            params, body_schema = [], None
            has_body = "requestBody" in op
            if has_body:
                content = resolve(spec, op["requestBody"]).get("content", {})
                media = next((m for m in content if "json" in m), None)
                if media:
                    body_schema = content[media].get("schema")
            for p in shared + op.get("parameters", []):
                p = resolve(spec, p)
                if p.get("in") == "body":  # Swagger 2 body parameter
                    has_body, body_schema = True, p.get("schema")
                    continue
                # Swagger 2 keeps the schema keywords on the parameter itself.
                schema = p.get("schema") or {k: v for k, v in p.items() if k in (
                    "type", "format", "enum", "minimum", "maximum", "minLength",
                    "maxLength", "items", "default")}
                params.append({"name": p.get("name"), "in": p.get("in"),
                               "type": schema.get("type", "string"),
                               "required": p.get("required", False),
                               "schema": schema})
            eps.append({"path": prefix + path, "method": method.upper(),
                        "params": params, "has_body": has_body,
                        "body_schema": body_schema, "auth": auth_of(spec, op)})
    return eps


# ── values from schemas ─────────────────────────────────────────────────────

def sample(schema, depth=0):
    """A plausible valid value for a schema: the base that body probes mutate."""
    schema = schema or {}
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    if schema.get("enum"):
        return schema["enum"][0]
    t = schema.get("type", "object" if "properties" in schema else "string")
    if t == "object" and depth < 4:
        return {k: sample(v, depth + 1) for k, v in schema.get("properties", {}).items()}
    if t == "array" and depth < 4:
        return [sample(schema.get("items"), depth + 1)]
    if t in ("integer", "number"):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        v = 1 if lo is None else lo
        return v if hi is None else min(v, hi)
    if t == "boolean":
        return True
    fmt = schema.get("format", "")
    if fmt == "date":
        return "2024-01-01"
    if fmt == "date-time":
        return "2024-01-01T00:00:00Z"
    if fmt == "uuid":
        return "00000000-0000-0000-0000-000000000001"
    if fmt == "email":
        return "qc@example.com"
    return "q" * max(schema.get("minLength", 0), 2)


def wrong_type(schema):
    t = (schema or {}).get("type", "string")
    return {"integer": "notanumber", "number": "notanumber", "boolean": "maybe",
            "array": "notalist", "object": "notanobject"}.get(t, {"qc": 1})


def boundaries(schema):
    """[(label, value, invalid)] at the schema's own limits. invalid=True means
    the schema forbids the value, so a 2xx is accepted-invalid; False means it
    is the last allowed value."""
    s = schema or {}
    out = []
    lo, hi = s.get("minimum"), s.get("maximum")
    if lo is not None:
        out += [(f"min-1={lo - 1}", lo - 1, True), (f"min={lo}", lo, False)]
    if hi is not None:
        out += [(f"max+1={hi + 1}", hi + 1, True), (f"max={hi}", hi, False)]
    if s.get("maxLength") is not None:
        n = s["maxLength"]
        out += [(f"maxLength+1={n + 1}", "A" * (n + 1), True),
                (f"maxLength={n}", "A" * n, False)]
    if s.get("minLength"):
        n = s["minLength"]
        out.append((f"minLength-1={n - 1}", "A" * (n - 1), True))
    if s.get("enum"):
        out.append(("not-in-enum", "qc_not_in_enum", True))
    bad_format = {"date": "2020-13-45", "date-time": "2020-13-45T25:61:00Z",
                  "uuid": "not-a-uuid", "email": "not-an-email",
                  "uri": "not a uri"}.get(s.get("format"))
    if bad_format:
        out.append((f"bad-{s['format']}", bad_format, True))
    return out


def fill_path(path, values, default):
    return re.sub(r"\{([^}]+)\}",
                  lambda m: urllib.parse.quote(str(values.get(m.group(1), default)), safe=""),
                  path)


# ── flags and records ───────────────────────────────────────────────────────

def accepted(rule, ep, probe, flag_name):
    return (rule.get("flag") == flag_name
            and rule.get("method", ep["method"]) == ep["method"]
            and rule.get("path", ep["path"]) == ep["path"]
            and probe.startswith(rule.get("probe", "")))


def flag(resp, probe, ep, marker=None, invalid=None):
    """Heuristic inline flags for the clear-cut cases Claude shouldn't miss,
    plus informational notes that are not findings on their own."""
    flags, notes = [], []
    s = resp.get("status")
    body = resp.get("body", "") or ""
    if s is None:
        err = (resp.get("error") or "").lower()
        flags.append("timeout" if "timed out" in err else "no-response")
    elif s >= 500:
        flags.append("server-error")
    elif resp.get("ms", 0) > SLOW_MS:
        flags.append("slow")
    low = body.lower()
    if any(sig in low for sig in LEAK_SIGNATURES) or LEAK_PATTERNS.search(low):
        flags.append("exception-leak")
    if marker and marker in body:
        ctype = {k.lower(): v for k, v in resp.get("headers", {}).items()}.get("content-type", "")
        if "json" in ctype:
            notes.append("marker echoed inside a JSON response (escape it wherever it is rendered)")
        else:
            flags.append("reflected-marker")
    ok = s is not None and 200 <= s < 300
    if probe.startswith("authz:") and ok and ep.get("auth") != "public":
        flags.append("auth-not-enforced")
    if invalid and ok:
        flags.append("accepted-invalid")
    return sorted(set(flags)), notes


def record(results, ep, probe, resp, marker=None, expect=None, invalid=None,
           sent=None, extra=None):
    flags, notes = flag(resp, probe, ep, marker, invalid)
    accepted_flags = [f for f in flags
                      if any(accepted(r, ep, probe, f) for r in ACCEPT_RULES)]
    row = {
        "path": ep["path"], "method": ep["method"], "auth": ep.get("auth"),
        "probe": probe, "marker": marker, "expected": expect,
        "url": resp.get("url"), "status": resp.get("status"), "ms": resp.get("ms"),
        "error": resp.get("error"),
        "flags": [f for f in flags if f not in accepted_flags],
        "body_snippet": (resp.get("body") or "")[:400],
    }
    if accepted_flags:
        row["accepted_flags"] = accepted_flags
    if notes:
        row["notes"] = notes
    if sent is not None:
        row["request_body"] = (sent if isinstance(sent, str) else json.dumps(sent))[:600]
    if probe.startswith("headers"):
        row["security_headers"] = {h: resp.get("headers", {}).get(h) for h in SECURITY_HEADERS}
    if extra:
        row.update(extra)
    results.append(row)


# ── mode runners ────────────────────────────────────────────────────────────

def _probeable(ep, allow_writes):
    """Safe methods always; write methods only when the caller opted in."""
    return ep["method"] in SAFE_METHODS or allow_writes


def _object_fields(ep):
    s = ep.get("body_schema") or {}
    if s.get("type", "object" if "properties" in s else None) != "object":
        return None, [], {}
    return s, s.get("required", []), s.get("properties", {})


def _send_body(ep, base, path, headers, caps, results, probe, body, expect,
               invalid=None, marker=None, raw=None):
    if not caps.take():
        return False
    r = do_request(ep["method"], build_url(base, path), headers=headers,
                   body=None if raw is not None else body, raw=raw)
    record(results, ep, probe, r, marker=marker, expect=expect, invalid=invalid,
           sent=raw if raw is not None else body)
    return True


def run_negative(ep, ctx):
    base, headers, caps, results = ctx["base"], ctx["headers"], ctx["caps"], ctx["results"]
    path = fill_path(ep["path"], {}, "0")  # templated ids get an obvious bad value
    # 1. Wrong HTTP method against the route.
    for m in ("GET", "POST", "DELETE"):
        if m == ep["method"] or (m not in SAFE_METHODS and not ctx["allow_writes"]):
            continue
        if not caps.take():
            return
        r = do_request(m, build_url(base, path), headers=headers)
        record(results, ep, f"negative:wrong-method:{m}", r, expect="405/404")
    safe = ep["method"] in SAFE_METHODS
    # 2. Wrong type in a templated id that the spec types as a number.
    if "{" in ep["path"] and safe and any(
            p["in"] == "path" and p["type"] in ("integer", "number") for p in ep["params"]):
        if caps.take():
            bad = fill_path(ep["path"], {}, "notanid")
            r = do_request(ep["method"], build_url(base, bad), headers=headers)
            record(results, ep, "negative:wrong-type-path", r, expect="400/404", invalid=True)
    good_path = fill_path(ep["path"], ctx["values"], "1")
    # 3. Missing *required* query params → send none. Optional-only endpoints
    #    legitimately return 200 here, so they are not probed.
    if safe and any(p["in"] == "query" and p.get("required") for p in ep["params"]):
        if caps.take():
            r = do_request(ep["method"], build_url(base, good_path), headers=headers)
            record(results, ep, "negative:missing-required-query", r, expect="400", invalid=True)
    # 4. Wrong type for typed query params only (a string param accepts anything).
    qp = {p["name"]: wrong_type(p["schema"]) for p in ep["params"]
          if p["in"] == "query" and p["name"] and p["type"] in ("integer", "number", "boolean")}
    if qp and safe and caps.take():
        r = do_request(ep["method"], build_url(base, good_path, qp), headers=headers)
        record(results, ep, "negative:wrong-type-query", r, expect="400", invalid=True)
    if not ep.get("has_body") or safe or not ctx["allow_writes"]:
        return
    # 5. Bodies: malformed JSON, then schema-aware invalid variants.
    for payload in ('{"bad": }', "not json at all", "[]", "null"):
        if not _send_body(ep, base, good_path, headers, caps, results,
                          "negative:malformed-body", None, "400", invalid=True, raw=payload):
            return
    schema, required, props = _object_fields(ep)
    if not schema:
        return
    full = sample(schema)
    if required and not _send_body(ep, base, good_path, headers, caps, results,
                                   "negative:body-empty-object", {}, "400", invalid=True):
        return
    for f in required:
        body = {k: v for k, v in full.items() if k != f}
        if not _send_body(ep, base, good_path, headers, caps, results,
                          f"negative:body-missing:{f}", body, "400", invalid=True):
            return
    for f, fs in props.items():
        if not _send_body(ep, base, good_path, headers, caps, results,
                          f"negative:body-wrong-type:{f}", {**full, f: wrong_type(fs)},
                          "400", invalid=True):
            return


def run_edge(ep, ctx):
    base, headers, caps, results = ctx["base"], ctx["headers"], ctx["caps"], ctx["results"]
    if not _probeable(ep, ctx["allow_writes"]):
        return
    # Templated path ids: schema limits first, then generic edge values.
    if "{" in ep["path"]:
        slots = [p for p in ep["params"] if p["in"] == "path"] or [{"name": None, "schema": {}}]
        for slot in slots:
            vals = [(label, v, inv) for label, v, inv in boundaries(slot["schema"])]
            vals += [(str(v)[:12], v, None) for v in
                     ["0", "-1", "999999999999", "abc", UNICODE[:8], OVERFLOW[:2000]]]
            for label, v, inv in vals:
                if not caps.take():
                    return
                values = dict(ctx["values"])
                if slot["name"]:
                    values[slot["name"]] = v
                p = fill_path(ep["path"], values, v)
                r = do_request(ep["method"], build_url(base, p), headers=headers)
                record(results, ep, f"edge:path:{slot['name'] or 'id'}={label}", r,
                       expect="400/404" if inv else "400/404 (not 500)", invalid=inv)
    path2 = fill_path(ep["path"], ctx["values"], "1")
    # Query param boundary values, one param at a time.
    for qp in [p for p in ep["params"] if p["in"] == "query" and p["name"]]:
        pool = EDGE_NUMBERS if qp["type"] in ("integer", "number") else EDGE_STRINGS
        vals = boundaries(qp["schema"]) + [(str(v)[:12], v, None) for v in pool]
        for label, v, inv in vals:
            if not caps.take():
                break
            r = do_request(ep["method"], build_url(base, path2, {qp["name"]: v}),
                           headers=headers)
            record(results, ep, f"edge:query:{qp['name']}={label}", r,
                   expect="400" if inv else "handled, no 500", invalid=inv)
    # Body fields at their limits (write routes only, so --allow-writes only).
    schema, required, props = _object_fields(ep)
    if not schema or ep["method"] in SAFE_METHODS:
        return
    full = sample(schema)
    for f, fs in props.items():
        vals = boundaries(fs)
        if f in required:
            vals.append(("null", None, True))
        generic = EDGE_NUMBERS[:4] if fs.get("type") in ("integer", "number") \
            else ["", WHITESPACE, OVERFLOW, UNICODE]
        vals += [(str(v)[:12] or "empty", v, None) for v in generic]
        for label, v, inv in vals:
            if not _send_body(ep, base, path2, headers, caps, results,
                              f"edge:body:{f}={label}", {**full, f: v},
                              "400" if inv else "handled, no 500", invalid=inv):
                return


def run_fuzz(ep, ctx):
    base, headers, caps, results, rnd = (ctx["base"], ctx["headers"], ctx["caps"],
                                         ctx["results"], ctx["rnd"])
    if not _probeable(ep, ctx["allow_writes"]):
        return
    qparams = [p for p in ep["params"] if p["in"] == "query" and p["name"]]
    schema, _, props = _object_fields(ep)
    with_body = schema and ep["method"] not in SAFE_METHODS
    full = sample(schema) if with_body else None
    for _ in range(ctx["iterations"]):
        if not caps.take():
            break
        q = {p["name"]: _fuzz_value(rnd) for p in qparams}
        p2 = fill_path(ep["path"], {}, _fuzz_value(rnd)[:20]) if "{" in ep["path"] else ep["path"]
        body = None
        if with_body:
            body = dict(full)
            for f in rnd.sample(list(props), k=rnd.randint(1, len(props))) if props else []:
                body[f] = rnd.choice([_fuzz_value(rnd), None, rnd.randint(-(2**40), 2**40),
                                      [_fuzz_value(rnd)], {"qc": _fuzz_value(rnd)}])
        r = do_request(ep["method"], build_url(base, p2, q), headers=headers, body=body)
        record(results, ep, "fuzz:body" if with_body else "fuzz:query", r,
               expect="no 500, no crash", sent=body)


def run_security(ep, ctx):
    base, headers, caps, results = ctx["base"], ctx["headers"], ctx["caps"], ctx["results"]
    auth_header = ctx["auth_header"]
    path = fill_path(ep["path"], ctx["values"], "1")
    # Required query params get a plausible value, so a request is turned away
    # by auth (or not) rather than by validation.
    needed = {p["name"]: ctx["values"].get(p["name"], sample(p["schema"])) for p in ep["params"]
              if p["in"] == "query" and p.get("required") and p["name"]}
    expect_auth = {"public": "2xx is fine (public by spec)"}.get(ep.get("auth"), "401/403")
    # 1. Authorization enforcement: no token, a junk token, a forged one.
    if ep["method"] in SAFE_METHODS:
        forged = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJxYyIsImV4cCI6MTAwMH0."  # alg none, expired
        for probe, hdrs in (("authz:no-token", {}),
                            ("authz:invalid-token", {auth_header: "Bearer not.a.real.token"}),
                            ("authz:forged-unsigned", {auth_header: f"Bearer {forged}"})):
            if not caps.take():
                return
            r = do_request(ep["method"], build_url(base, path, needed or None), headers=hdrs)
            record(results, ep, probe, r, expect=expect_auth)
    # 2. Injection markers in query params — confirm rejection/sanitisation.
    markers = SQLI_MARKERS[:2] + XSS_MARKERS[:2] + PATH_MARKERS[:1]
    if ep["method"] in SAFE_METHODS:
        for qp in [p for p in ep["params"] if p["in"] == "query" and p["name"]]:
            for marker in markers:
                if not caps.take():
                    return
                r = do_request(ep["method"], build_url(base, path, {**needed, qp["name"]: marker}),
                               headers=headers)
                record(results, ep, f"security:injection-marker:{qp['name']}", r,
                       marker=marker, expect="rejected/sanitised, never 500 or reflected")
    # ... and in string body fields of write routes (--allow-writes only).
    schema, _, props = _object_fields(ep)
    if schema and ctx["allow_writes"] and ep["method"] not in SAFE_METHODS:
        full = sample(schema)
        for f in [f for f, fs in props.items() if fs.get("type", "string") == "string"]:
            for marker in markers:
                if not _send_body(ep, base, path, headers, caps, results,
                                  f"security:injection-marker:body.{f}", {**full, f: marker},
                                  "rejected/sanitised, never 500 or reflected", marker=marker):
                    return
    # 3. Security headers / info leakage on a normal request.
    if caps.take():
        r = do_request(ep["method"] if ep["method"] in SAFE_METHODS else "GET",
                       build_url(base, path, needed or None), headers=headers)
        record(results, ep, "headers:baseline", r, expect="security headers present")


def run_scope(ep, ctx):
    """Same request as user A, then as user B. Paths are filled only from
    --value (ids that belong to A), so a 2xx for B there is a real signal."""
    if ep["method"] not in SAFE_METHODS:
        return
    names = re.findall(r"\{([^}]+)\}", ep["path"])
    required_q = [p["name"] for p in ep["params"] if p["in"] == "query" and p.get("required")]
    missing = [n for n in names + required_q if n not in ctx["values"]]
    if missing:
        ctx["skipped"].append({"method": ep["method"], "path": ep["path"], "needs": missing})
        return
    url = build_url(ctx["base"], fill_path(ep["path"], ctx["values"], ""),
                    {n: ctx["values"][n] for n in required_q} or None)
    if not ctx["caps"].take():
        return
    a = do_request(ep["method"], url, headers=ctx["headers"])
    if not ctx["caps"].take():
        return
    b = do_request(ep["method"], url, headers=ctx["headers_b"])
    a_ok = a["status"] is not None and 200 <= a["status"] < 300
    b_ok = b["status"] is not None and 200 <= b["status"] < 300
    same = a_ok and b_ok and a["body"] == b["body"]
    record(ctx["results"], ep, "scope:a-vs-b", b,
           expect="B gets 403/404, or only B's own data",
           extra={"status_a": a["status"], "len_a": len(a["body"] or ""),
                  "len_b": len(b["body"] or ""), "same_body": same})
    row = ctx["results"][-1]
    new = []
    if names and a_ok and b_ok:
        new.append("cross-user-access")
    elif not names and same and len((a["body"] or "").strip()) >= 16:  # skip {"ok":true}-size bodies
        new.append("same-data-both-users")
    for f in new:
        if any(accepted(r, ep, "scope:a-vs-b", f) for r in ACCEPT_RULES):
            row.setdefault("accepted_flags", []).append(f)
        else:
            row["flags"] = sorted(set(row["flags"] + [f]))


def build_url(base, path, query=None):
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def _rand_str(rnd, n):
    alph = "abcdefghijklmnopqrstuvwxyz0123456789-_./'\"<>%"
    return "".join(rnd.choice(alph) for _ in range(n))


def _fuzz_value(rnd):
    kind = rnd.randint(0, 6)
    if kind == 0:
        return _rand_str(rnd, rnd.randint(1, 64))
    if kind == 1:
        return str(rnd.randint(-(2**40), 2**40))
    if kind == 2:
        return rnd.choice(SQLI_MARKERS + XSS_MARKERS + PATH_MARKERS)
    if kind == 3:
        return UNICODE
    if kind == 4:
        return "A" * rnd.randint(1000, 8000)
    if kind == 5:
        return rnd.choice(["", " ", "null", "undefined", "NaN", "0", "-1"])
    return urllib.parse.quote(_rand_str(rnd, 20))


def main():
    global TIMEOUT, SLOW_MS, ACCEPT_RULES
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="base URL, e.g. http://localhost:5040")
    ap.add_argument("--mode", required=True,
                    choices=["negative", "edge", "fuzz", "security", "scope"])
    ap.add_argument("--out", required=True, help="JSON results file to write")
    ap.add_argument("--openapi", "--swagger", dest="openapi",
                    help="OpenAPI URL or local file (default: try common paths)")
    ap.add_argument("--endpoints", help="JSON endpoint list instead of OpenAPI")
    ap.add_argument("--token-env", default="QC_TOKEN",
                    help="env var holding a user-supplied token (default QC_TOKEN)")
    ap.add_argument("--token-env-b", default="QC_TOKEN_B",
                    help="env var with a second user's token, for --mode scope")
    ap.add_argument("--auth-header", default="Authorization",
                    help="header the token goes in (default Authorization)")
    ap.add_argument("--auth-scheme", default="Bearer",
                    help="token prefix; '' sends the raw token (API keys)")
    ap.add_argument("--value", action="append", default=[], metavar="NAME=VALUE",
                    help="real value for a path param or required query param "
                         "(repeatable); scope mode uses these as user A's ids")
    ap.add_argument("--only", help="substring filter on path")
    ap.add_argument("--allow-writes", action="store_true")
    ap.add_argument("--max-requests", type=int, default=300)
    ap.add_argument("--iterations", type=int, default=25)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--timeout", type=float, default=8.0, help="seconds per request")
    ap.add_argument("--slow-ms", type=int, default=3000, help="flag answers slower than this")
    ap.add_argument("--delay-ms", type=int, default=0, help="pause between requests")
    ap.add_argument("--accept", help="JSON file of accepted findings (see above)")
    ap.add_argument("--allow-host", action="append", default=[])
    ap.add_argument("--fail-on-flags", action="store_true",
                    help="exit 1 when any result is flagged (for one-command gates)")
    args = ap.parse_args()
    TIMEOUT, SLOW_MS = args.timeout, args.slow_ms

    if not host_allowed(args.base, set(args.allow_host)):
        print(f"REFUSED: {args.base} is not localhost and not in --allow-host. "
              "Probes only run against a service you started or a host you "
              "explicitly authorise.", file=sys.stderr)
        sys.exit(2)
    if args.accept:
        with open(args.accept, encoding="utf-8") as f:
            ACCEPT_RULES = json.load(f)

    def auth_headers(env):
        token = os.environ.get(env)
        if not token:
            return {}
        return {args.auth_header: f"{args.auth_scheme} {token.strip()}".strip()}

    headers, headers_b = auth_headers(args.token_env), auth_headers(args.token_env_b)
    if args.mode == "scope" and not (headers and headers_b):
        print(f"scope mode needs two users' tokens: set {args.token_env} (user A) and "
              f"{args.token_env_b} (user B)", file=sys.stderr)
        sys.exit(2)
    values = {}
    for kv in args.value:
        name, sep, val = kv.partition("=")
        if not sep:
            print(f"--value expects NAME=VALUE, got {kv!r}", file=sys.stderr)
            sys.exit(2)
        values[name] = val

    endpoints = load_endpoints(args)
    if args.only:
        endpoints = [e for e in endpoints if args.only in e["path"]]
    if not endpoints:
        print("no endpoints resolved (no OpenAPI document or empty --only filter)",
              file=sys.stderr)
        sys.exit(1)

    ctx = {"base": args.base, "headers": headers, "headers_b": headers_b,
           "caps": Caps(args.max_requests, args.delay_ms), "results": [],
           "allow_writes": args.allow_writes, "iterations": args.iterations,
           "rnd": random.Random(args.seed), "auth_header": args.auth_header,
           "values": values, "skipped": []}
    runner = {"negative": run_negative, "edge": run_edge, "fuzz": run_fuzz,
              "security": run_security, "scope": run_scope}[args.mode]
    caps = ctx["caps"]
    for ep in endpoints:
        if caps.used >= caps.limit:
            break
        runner(ep, ctx)

    results = ctx["results"]
    flagged = [r for r in results if r["flags"]]
    out = {
        "meta": {
            "base": args.base, "mode": args.mode, "generated": iso_now(),
            "endpoints": len(endpoints), "requests": caps.used,
            "cap_reached": caps.used >= caps.limit,
            "authenticated": bool(headers), "allow_writes": args.allow_writes,
            "seed": args.seed, "flagged": len(flagged),
            "accepted": sum(1 for r in results if r.get("accepted_flags")),
            "auth_from_spec": sorted({e.get("auth", "unknown") for e in endpoints}),
            "skipped": ctx["skipped"],
        },
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"{args.mode}: {caps.used} requests over {len(endpoints)} endpoints, "
          f"{len(flagged)} flagged → {args.out}"
          + (" (request cap reached: later endpoints were not probed)"
             if out["meta"]["cap_reached"] else "")
          + (f"; {len(ctx['skipped'])} endpoints skipped, need --value"
             if ctx["skipped"] else ""))
    # Flags are heuristics for the caller to judge; only --fail-on-flags turns
    # them into an exit status, so a plain run never masquerades as a gate.
    sys.exit(1 if (args.fail_on_flags and flagged) else 0)


if __name__ == "__main__":
    main()
