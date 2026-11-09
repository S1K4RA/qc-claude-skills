---
name: qc-security
description: >-
  Use when the user wants defensive security QC of an API or app their team
  owns — does it require a valid token, reject forged or expired JWTs, keep
  one user out of another's data, reject or escape SQL-injection and XSS
  markers, set security headers, avoid leaking stack traces — or says
  "security test", "is this endpoint protected", "authz test", "IDOR", or
  needs a token for authenticated tests (never minted from signing keys).
  Local or authorised non-production only; no exploits, no DoS.
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
supplies them in `QC_TOKEN` / `QC_TOKEN_B`, or the variables the project's
`.claude/qc-profile.md` names). If asked to read a signing key or secret
config to make a token, decline and ask the user to export one instead.

## What it checks (HTTP)

1. **Authorization enforcement** (`--mode security`): each route is hit with
   no token, an invalid token, and a forged **unsigned** (`alg:none`), expired
   token, with its required params filled so auth, not validation, decides.
   Whether a route is protected comes from the spec's `security`. A 2xx on a
   protected route is `auth-not-enforced`, high severity.
2. **Scope between users** (`--mode scope`, needs two users' tokens): the same
   request as user A and as user B. Pass A's real ids with `--value id=<A's
   id>`: a 2xx for B there is `cross-user-access` (IDOR), the most serious
   finding. On list routes without ids, identical non-trivial data for both is
   `same-data-both-users`: a bug for per-user data, correct for shared
   reference data. Routes whose ids you didn't supply are listed as skipped.
3. **Input validation**: SQL-injection and XSS **markers** in query params
   (and, with `--allow-writes`, in string body fields). `reflected-marker` is
   a marker returned raw in an HTML/text body; a marker echoed inside JSON is
   only a note (escape it where it is rendered). A `server-error` or
   `exception-leak` on a marker is a finding; a clean 400 is the pass.
4. **Information leakage**: stack traces, SQL errors, and framework internals
   across common stacks (`exception-leak`).
5. **Security headers**: listed per endpoint by `summarize.py`.

## Run it

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # or set BASE to an authorised host
[[ -n "${BASE:-}" ]] || exit 1
# tokens are user-supplied: export QC_TOKEN=<user A>  QC_TOKEN_B=<user B>
# probe.py sends QC_TOKEN whenever it is set: for an unauthenticated run, `unset QC_TOKEN` first
python3 <qc>/scripts/probe.py --base "$BASE" --mode security \
  --out "<scratchpad>/qc-<svc>-security.json" [--only /api/<group>] [--allow-host <host>] [--delay-ms <ms>]
python3 <qc>/scripts/probe.py --base "$BASE" --mode scope --value id=<A's id> \
  --out "<scratchpad>/qc-<svc>-scope.json" [--only /api/<group>] [--delay-ms <ms>]  # when both tokens are set
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
python3 <qc>/scripts/summarize.py "<scratchpad>"/qc-<svc>-{security,scope}.json [--accept <file>]
```

For API keys instead of bearer tokens: `--auth-header X-Api-Key --auth-scheme ''`.

Rank cross-user access and `auth-not-enforced` highest, then reflected markers
and exception leaks, then missing headers. A route the spec doesn't mark
either way shows `auth: unknown`: read its auth attributes or middleware, and
list it as **needs decision** if the code doesn't settle it. Check the profile
for known data gaps that make a 403 expected before calling it a bug.

## Code targets
Per `<qc>/references/targets.md`: token storage and transport, route /
deep-link parameter rejection, output escaping (XSS markers escaped, not
executed), and no sensitive data in logs or error states. Never exfiltrate;
never touch secret config.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-security.md`), worst first with severity,
concrete evidence, and the fix the finding points to. Offer to turn confirmed
findings into tracked work, and accept rules for what is by design.
