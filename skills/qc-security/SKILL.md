---
name: qc-security
description: >-
  Security QC of an API, service, or app the user's team owns — authorization
  enforcement, input validation, and information leakage. Use when the user
  wants to check that an endpoint or service requires a valid token, rejects
  missing / invalid / forged-unsigned / expired JWTs, enforces role and scope,
  rejects or sanitises SQL-injection and XSS markers instead of reflecting or
  500-ing on them, sets security response headers, and never leaks stack
  traces or SQL errors. Triggers on "security test", "is this endpoint
  protected", "can I call this without a token", "check for SQL injection /
  XSS", "does it leak errors", "authz test", "harden the auth service". This
  is authorised defensive QC on your own code — non-destructive, local or
  authorised non-production only, no secrets, no exploit chaining, no DoS.
  For plain bad input use qc-negative, boundaries qc-edge, random junk
  qc-fuzz, and all four at once qc.
---

# Security QC

This is **defensive security testing of the team's own code**, to find
authorization and input-validation gaps before users do. It confirms the API
does the safe thing; it does not build working exploits, chain
vulnerabilities, or extract data. A flagged marker is a *signal to fix
validation*, which the report hands back to the team.

`<qc>` is the `qc` skill's directory, next to this one (`../qc` from this
skill's base directory). **Read `<qc>/references/scope-and-safety.md` first —
this skill depends on it.** Local service you started, or an authorised
non-production host the user names (`--allow-host`); non-destructive; never
read, print, or edit secret/config files; never mint a token (the user
supplies one in `QC_TOKEN`, or the variable the project's
`.claude/qc-profile.md` names).

## What it checks (HTTP)

1. **Authorization enforcement**: each protected route is hit with no token,
   an invalid token, and a forged **unsigned** (`alg:none`), expired token;
   each should return 401/403. A 2xx on an unauthenticated protected route is
   flagged `auth-not-enforced` and is a high-severity finding.
2. **Input validation**: SQL-injection and XSS **markers** are sent in
   parameters to confirm the API rejects or sanitises them. A
   `reflected-marker` (the raw marker comes back in the body) or a
   `server-error`/`exception-leak` on a marker is a finding; a clean 400 is the
   pass.
3. **Information leakage**: responses are scanned for stack traces, SQL
   errors, and framework internals across common stacks (`exception-leak`).
4. **Security headers**: presence of `X-Content-Type-Options`,
   `X-Frame-Options`, `Content-Security-Policy`, HSTS, and sane
   `Cache-Control`.

## Run it

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # or set BASE to an authorised host
# authenticated checks need a user-supplied token:
#   export QC_TOKEN=<token copied from a logged-in session>
python3 <qc>/scripts/probe.py --base "$BASE" --mode security \
  --out "<scratchpad>/qc-<svc>-security.json" [--only /api/<group>] \
  [--allow-host <host>] [--auth-header X-Api-Key --auth-scheme ''] [--fail-on-flags]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
```

Then read the JSON and judge each result against its `expected`. Rank authz
gaps (auth not enforced, role/scope not checked) highest, then reflected
markers and exception leaks, then missing headers. A 2xx on a route that is
public by design is not a finding: read the route's auth attributes or
middleware to learn its intent, and list it as **needs decision** when the
code and profile don't say. Missing headers are not flagged by the engine;
read `security_headers` on the `headers:baseline` results.

**Role / scope checks** often need two tokens (e.g. a regular user vs. an
admin, or users of two tenants) to prove a low-privilege user cannot reach a
high-privilege route or another tenant's data. Ask the user to supply both
when scope enforcement is in question, and check the profile for known data
gaps that make a 403 expected before calling it a bug.

## Code targets
Per `<qc>/references/targets.md`: token storage and transport, route /
deep-link parameter rejection, output escaping (XSS markers escaped, not
executed), and no sensitive data in logs or error states. Never exfiltrate;
never touch secret config.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-security.md`), worst first with severity,
concrete evidence, and the fix the finding points to. Offer to turn confirmed
findings into tracked work so they are re-checkable.
