#!/usr/bin/env python3
"""Black-box HTTP probe engine for QC of an API you own.

Stdlib only (urllib) so it runs on any Python 3.8+ with no pip installs.

It reads a target's OpenAPI document (OpenAPI 3 or Swagger 2, JSON; YAML too
when PyYAML happens to be installed) or an explicit endpoint list, then sends a
bounded set of *non-destructive* probes per mode and records what came back.
It does not decide pass/fail on its own for the subtle cases — it emits
structured JSON that the calling skill (Claude) reads and judges against the
endpoint's intended behaviour. Clear-cut signals (a 5xx, a stack trace in the
body, a reflected payload) are flagged inline so nothing gets lost.

Modes:
  negative  wrong types, missing required fields, bad methods, malformed bodies
  edge      boundary values: empty, huge, zero, negative, unicode, whitespace
  fuzz      random/structured junk in each parameter, many iterations
  security  authz gaps (no/invalid/forged token), injection *markers* that
            should be rejected or sanitised, security headers, info leakage

Scope & safety (enforced here, not just documented):
  - Only http(s)://localhost, 127.0.0.1, or a host passed via --allow-host.
    Any other host aborts. This keeps probes on a service you started or an
    environment you explicitly authorised.
  - Read-only by default. State-changing methods (POST/PUT/PATCH/DELETE) are
    skipped in every mode unless --allow-writes is given, and even then the
    payloads are the invalid/garbage kind meant to be *rejected*, never valid
    mutations. Use it only against a local, throwaway database.
  - A global request cap and per-request timeout keep this from ever looking
    like a load or denial-of-service test. This is correctness QC, not stress.

Endpoint list format (--endpoints), when there is no OpenAPI document:
  [{"path": "/api/items/{id}", "method": "GET", "has_body": false,
    "params": [{"name": "page", "in": "query", "type": "integer",
                "required": false}]}]

Usage:
  probe.py --base http://localhost:5040 --mode negative --out results.json \
           [--openapi <url-or-file>] [--endpoints endpoints.json] \
           [--token-env QC_TOKEN] [--auth-header Authorization] \
           [--auth-scheme Bearer] [--only /api/items] [--allow-writes] \
           [--max-requests 300] [--iterations 25] [--seed 1337] \
           [--allow-host dev.internal] [--fail-on-flags]
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

# Where frameworks usually serve their OpenAPI document, tried in order when
# --openapi is not given (ASP.NET, FastAPI, Spring, Express/Nest, misc).
OPENAPI_PATHS = ["/swagger/v1/swagger.json", "/openapi.json", "/v3/api-docs",
                 "/swagger.json", "/api-docs", "/api/openapi.json",
                 "/docs/openapi.json", "/api-docs.json", "/openapi.yaml"]

# Lower-cased substrings that mean a response leaked internals: stack traces
# and SQL/driver errors across the common server stacks.
LEAK_SIGNATURES = (
    # generic
    "stack trace", "stacktrace", "unhandled exception", "internal server error:",
    # .NET
    "at system.", "microsoft.data.sqlclient", "sqlexception",
    "nullreferenceexception", "dbupdateexception", "   at ",
    # Java / Kotlin / Spring
    "java.lang.", "at org.", "at com.", "exception in thread",
    "org.hibernate", "jdbc",
    # Python
    "traceback (most recent call last)", "psycopg2", "sqlalchemy.exc",
    "sqlite3.operationalerror", "django.db",
    # Node
    "at object.<anonymous>", "at module._compile", "node_modules/",
    "unhandledpromiserejection",
    # PHP / Ruby / Go
    "fatal error:", "on line ", "pdoexception", "activerecord::",
    "goroutine ", "panic:",
    # SQL engines
    "sqlstate", "ora-0", "pg::", "syntax error at or near",
    "you have an error in your sql syntax", "unclosed quotation mark",
)


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
    """Global request budget so a run can never turn into a load test."""
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def take(self):
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def do_request(method, url, body=None, headers=None, timeout=8, raw=None, limit=8192):
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
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
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
                "headers": {}, "body": "", "error": str(e)}


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


def load_endpoints(args):
    """Return [{path, method, params:[{name, in, type, required}], has_body}]."""
    if args.endpoints:
        with open(args.endpoints, encoding="utf-8") as f:
            return json.load(f)
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
        shared = item.get("parameters", [])  # path-level parameters
        for method, op in item.items():
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                continue
            params = []
            has_body = "requestBody" in op
            for p in shared + op.get("parameters", []):
                if "$ref" in p:
                    continue
                if p.get("in") == "body":  # Swagger 2 body parameter
                    has_body = True
                    continue
                params.append({"name": p.get("name"), "in": p.get("in"),
                               "type": p.get("type") or (p.get("schema") or {}).get("type", "string"),
                               "required": p.get("required", False)})
            eps.append({"path": prefix + path, "method": method.upper(),
                        "params": params, "has_body": has_body})
    return eps


def build_url(base, path, query=None):
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def flag(resp, probe_kind, marker=None):
    """Heuristic inline flags for the clear-cut cases Claude shouldn't miss."""
    flags = []
    s = resp.get("status")
    body = resp.get("body", "") or ""
    if s is not None and s >= 500:
        flags.append("server-error")
    low = body.lower()
    if any(sig in low for sig in LEAK_SIGNATURES):
        flags.append("exception-leak")
    if marker and marker in body:
        flags.append("reflected-marker")
    if probe_kind == "authz" and s is not None and 200 <= s < 300:
        flags.append("auth-not-enforced")
    return sorted(set(flags))


def record(results, ep, probe, resp, marker=None, expect=None):
    results.append({
        "path": ep["path"], "method": ep["method"],
        "probe": probe, "marker": marker, "expected": expect,
        "url": resp.get("url"),
        "status": resp.get("status"), "ms": resp.get("ms"),
        "error": resp.get("error"),
        "flags": flag(resp, probe.split(":")[0], marker),
        "body_snippet": (resp.get("body") or "")[:400],
        "security_headers": {h: resp.get("headers", {}).get(h) for h in (
            "Strict-Transport-Security", "X-Content-Type-Options",
            "X-Frame-Options", "Content-Security-Policy",
            "Cache-Control") } if probe.startswith("headers") else None,
    })


# ── mode runners ────────────────────────────────────────────────────────────

def run_negative(ep, base, headers, caps, results, allow_writes):
    path = ep["path"]
    if "{" in path:  # fill templated ids with an obvious bad value
        path = re.sub(r"\{[^}]+\}", "0", path)
    # 1. Wrong HTTP method against the route.
    for m in ("GET", "POST", "DELETE"):
        if m == ep["method"]:
            continue
        if m not in SAFE_METHODS and not allow_writes:
            continue
        if not caps.take():
            return
        r = do_request(m, build_url(base, path), headers=headers)
        record(results, ep, f"negative:wrong-method:{m}", r, expect="405/404")
    # 2. Wrong type in a templated id that the spec types as a number.
    if "{" in ep["path"] and ep["method"] in SAFE_METHODS and any(
            p["in"] == "path" and p["type"] in ("integer", "number")
            for p in ep["params"]):
        if caps.take():
            bad = re.sub(r"\{[^}]+\}", "notanid", ep["path"])
            r = do_request(ep["method"], build_url(base, bad), headers=headers)
            record(results, ep, "negative:wrong-type-path", r, expect="400/404")
    # 3. Missing *required* query params → send none. Optional-only endpoints
    #    legitimately return 200 here, so they are not probed.
    if ep["method"] in SAFE_METHODS and any(
            p["in"] == "query" and p.get("required") for p in ep["params"]):
        if caps.take():
            r = do_request(ep["method"], build_url(base, path), headers=headers)
            record(results, ep, "negative:missing-required-query", r, expect="400")
    # 4. Wrong type for typed query params only (a string param accepts anything).
    qp = {}
    for p in ep["params"]:
        if p["in"] != "query" or not p["name"]:
            continue
        if p["type"] in ("integer", "number"):
            qp[p["name"]] = "notanumber"
        elif p["type"] == "boolean":
            qp[p["name"]] = "maybe"
    if qp and ep["method"] in SAFE_METHODS and caps.take():
        r = do_request(ep["method"], build_url(base, path, qp), headers=headers)
        record(results, ep, "negative:wrong-type-query", r, expect="400")
    # 5. Malformed JSON body on write endpoints (garbage, rejected not stored).
    if ep.get("has_body") and allow_writes and ep["method"] not in SAFE_METHODS:
        for payload in ('{"bad": }', "not json at all", "[]", "null"):
            if not caps.take():
                return
            r = do_request(ep["method"], build_url(base, path), headers=headers,
                           raw=payload)
            record(results, ep, "negative:malformed-body", r, expect="400")


def _probeable(ep, allow_writes):
    """Safe methods always; write methods only when the caller opted in."""
    return ep["method"] in SAFE_METHODS or allow_writes


def run_edge(ep, base, headers, caps, results, allow_writes):
    path = ep["path"]
    qparams = [p for p in ep["params"] if p["in"] == "query" and p["name"]]
    if not _probeable(ep, allow_writes):
        return
    # Template path ids: try edge values in the id slot.
    if "{" in ep["path"]:
        for val in ["0", "-1", "999999999999", "abc", UNICODE[:8], OVERFLOW[:2000]]:
            if not caps.take():
                return
            p = re.sub(r"\{[^}]+\}", urllib.parse.quote(val, safe=""), ep["path"])
            r = do_request(ep["method"], build_url(base, p), headers=headers)
            record(results, ep, f"edge:path-id:{val[:12]}", r, expect="400/404 (not 500)")
    # Query param boundary values, one param at a time.
    for qp in qparams:
        pool = EDGE_NUMBERS if qp["type"] in ("integer", "number") else EDGE_STRINGS
        for val in pool:
            if not caps.take():
                break
            path2 = re.sub(r"\{[^}]+\}", "1", path)
            r = do_request(ep["method"], build_url(base, path2, {qp["name"]: val}),
                           headers=headers)
            record(results, ep, f"edge:query:{qp['name']}={str(val)[:12]}", r,
                   expect="handled, no 500")


def run_fuzz(ep, base, headers, caps, results, allow_writes, iterations, rnd):
    qparams = [p for p in ep["params"] if p["in"] == "query" and p["name"]]
    if not _probeable(ep, allow_writes):
        return
    for _ in range(iterations):
        if not caps.take():
            break
        q = {p["name"]: _fuzz_value(rnd) for p in qparams}
        p2 = re.sub(r"\{[^}]+\}", urllib.parse.quote(_fuzz_value(rnd)[:20], safe=""),
                    ep["path"]) if "{" in ep["path"] else ep["path"]
        r = do_request(ep["method"], build_url(base, p2, q), headers=headers)
        record(results, ep, "fuzz:query", r, expect="no 500, no crash")


def run_security(ep, base, headers, caps, results, allow_writes, auth_header):
    path = re.sub(r"\{[^}]+\}", "1", ep["path"]) if "{" in ep["path"] else ep["path"]
    # 1. Authorization enforcement: hit the endpoint with no token and a junk token.
    if ep["method"] in SAFE_METHODS:
        if caps.take():
            r = do_request(ep["method"], build_url(base, path), headers={})
            record(results, ep, "authz:no-token", r, expect="401 (if protected)")
        if caps.take():
            bad = {auth_header: "Bearer not.a.real.token"}
            r = do_request(ep["method"], build_url(base, path), headers=bad)
            record(results, ep, "authz:invalid-token", r, expect="401")
        # Forged, structurally valid JWT: unsigned (alg none) and long expired.
        if caps.take():
            forged = ("eyJhbGciOiJub25lIn0.eyJzdWIiOiJxYyIsImV4cCI6MTAwMH0.")
            r = do_request(ep["method"], build_url(base, path),
                           headers={auth_header: f"Bearer {forged}"})
            record(results, ep, "authz:forged-unsigned", r, expect="401")
    # 2. Injection markers in query params — confirm rejection/sanitisation.
    for qp in [p for p in ep["params"] if p["in"] == "query" and p["name"]]:
        for marker in SQLI_MARKERS[:2] + XSS_MARKERS[:2] + PATH_MARKERS[:1]:
            if ep["method"] not in SAFE_METHODS or not caps.take():
                break
            r = do_request(ep["method"], build_url(base, path, {qp["name"]: marker}),
                           headers=headers)
            record(results, ep, f"security:injection-marker:{qp['name']}", r,
                   marker=marker, expect="rejected/sanitised, never 500 or reflected")
    # 3. Security headers / info leakage on a normal request.
    if caps.take():
        r = do_request(ep["method"] if ep["method"] in SAFE_METHODS else "GET",
                       build_url(base, path), headers=headers)
        record(results, ep, "headers:baseline", r, expect="security headers present")


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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="base URL, e.g. http://localhost:5040")
    ap.add_argument("--mode", required=True,
                    choices=["negative", "edge", "fuzz", "security"])
    ap.add_argument("--out", required=True, help="JSON results file to write")
    ap.add_argument("--openapi", "--swagger", dest="openapi",
                    help="OpenAPI URL or local file (default: try common paths)")
    ap.add_argument("--endpoints", help="JSON endpoint list instead of OpenAPI")
    ap.add_argument("--token-env", default="QC_TOKEN",
                    help="env var holding a user-supplied token (default QC_TOKEN)")
    ap.add_argument("--auth-header", default="Authorization",
                    help="header the token goes in (default Authorization)")
    ap.add_argument("--auth-scheme", default="Bearer",
                    help="token prefix; '' sends the raw token (API keys)")
    ap.add_argument("--only", help="substring filter on path")
    ap.add_argument("--allow-writes", action="store_true")
    ap.add_argument("--max-requests", type=int, default=300)
    ap.add_argument("--iterations", type=int, default=25)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--allow-host", action="append", default=[])
    ap.add_argument("--fail-on-flags", action="store_true",
                    help="exit 1 when any result is flagged (for one-command gates)")
    args = ap.parse_args()

    if not host_allowed(args.base, set(args.allow_host)):
        print(f"REFUSED: {args.base} is not localhost and not in --allow-host. "
              "Probes only run against a service you started or a host you "
              "explicitly authorise.", file=sys.stderr)
        sys.exit(2)

    headers = {}
    token = os.environ.get(args.token_env)
    if token:
        headers[args.auth_header] = f"{args.auth_scheme} {token.strip()}".strip()

    endpoints = load_endpoints(args)
    if args.only:
        endpoints = [e for e in endpoints if args.only in e["path"]]
    if not endpoints:
        print("no endpoints resolved (no OpenAPI document or empty --only filter)",
              file=sys.stderr)
        sys.exit(1)

    caps = Caps(args.max_requests)
    rnd = random.Random(args.seed)
    results = []
    for ep in endpoints:
        if caps.used >= caps.limit:
            break
        if args.mode == "negative":
            run_negative(ep, args.base, headers, caps, results, args.allow_writes)
        elif args.mode == "edge":
            run_edge(ep, args.base, headers, caps, results, args.allow_writes)
        elif args.mode == "fuzz":
            run_fuzz(ep, args.base, headers, caps, results, args.allow_writes,
                     args.iterations, rnd)
        elif args.mode == "security":
            run_security(ep, args.base, headers, caps, results, args.allow_writes,
                         args.auth_header)

    flagged = [r for r in results if r["flags"]]
    out = {
        "meta": {
            "base": args.base, "mode": args.mode, "generated": iso_now(),
            "endpoints": len(endpoints), "requests": caps.used,
            "authenticated": bool(token), "allow_writes": args.allow_writes,
            "seed": args.seed, "flagged": len(flagged),
        },
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"{args.mode}: {caps.used} requests over {len(endpoints)} endpoints, "
          f"{len(flagged)} flagged → {args.out}")
    # Flags are heuristics for the caller to judge; only --fail-on-flags turns
    # them into an exit status, so a plain run never masquerades as a gate.
    sys.exit(1 if (args.fail_on_flags and flagged) else 0)


if __name__ == "__main__":
    main()
