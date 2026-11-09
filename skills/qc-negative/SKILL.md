---
name: qc-negative
description: >-
  Use when the user wants to check how an endpoint, service, app, or library
  they own handles invalid input — wrong method, missing required fields,
  wrong types, malformed JSON — or says "negative test", "error paths", "does
  it reject bad input", "fail gracefully". Boundaries: qc-edge. Random junk:
  qc-fuzz. Authz and injection: qc-security. All four: qc.
---

# Negative testing

Confirms a target **refuses invalid input correctly**: the right 4xx, a clear
message, no server error, no accidental success. A 500, or a 2xx where a 400
was due, is the bug.

`<qc>` is the `qc` skill's directory, next to this one (`../qc` from this
skill's base directory). Read `<qc>/references/scope-and-safety.md` first,
then `<qc>/references/targets.md`, and the project's `.claude/qc-profile.md`
if it has one.

## HTTP service

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
[[ -n "${BASE:-}" ]] || exit 1
# probe.py sends QC_TOKEN whenever it is set: for an unauthenticated run, `unset QC_TOKEN` first
python3 <qc>/scripts/probe.py --base "$BASE" --mode negative \
  --out "<scratchpad>/qc-<svc>-negative.json" [--only /api/<group>] [--allow-writes] [--delay-ms <ms>]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
python3 <qc>/scripts/summarize.py "<scratchpad>/qc-<svc>-negative.json"
```

Probes: wrong method, non-numeric ids where the spec says integer, missing
*required* query params, wrong types on typed params. With `--allow-writes`
(local throwaway DB only), write routes also get malformed JSON and
schema-driven bodies: an empty object, each required field dropped, each field
given the wrong type. Every one of these is invalid, so a 2xx is flagged
`accepted-invalid`.

Judge from the summary. A finding is a `server-error`, `exception-leak`, or
`accepted-invalid`. A correct 400/404/405/422 is a pass and goes in the
coverage summary.

## Code targets

Per the targets reference: run the existing suite first, then copy the
stack's template from `<qc>/templates/` into a `qc_tmp` folder inside the
project and fill its negative group for the parsers, validators, and stores in
scope (missing fields, wrong types, nulls, malformed API responses). Delete it
afterwards unless the user wants the tests kept.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-negative.md`), worst first, with the
coverage summary. Offer to turn confirmed failures into tracked work.

## Common mistakes
- Calling a 401 a failure when no token was exported: that is auth working.
  Body probes need a token for protected write routes, or they only test auth.
- Running `--allow-writes` against shared data.
