---
name: qc-negative
description: >-
  Use when the user wants to check how an API endpoint or service, an app, or
  a library they own behaves on invalid input — wrong HTTP method, missing
  required fields, wrong types, malformed JSON, bad auth — or says "negative
  test", "error paths", "does it reject bad input", "fail gracefully", "send
  garbage to this endpoint". Boundary values: qc-edge. Random junk at volume:
  qc-fuzz. Authz and injection: qc-security. All four at once: qc.
---

# Negative testing

Confirms a target **refuses invalid input correctly**: the right 4xx, a clear
message, no server error, no accidental success. A 500, or a 200 where a 400
was due, is the bug.

`<qc>` is the `qc` skill's directory, next to this one (`../qc` from this
skill's base directory). Read `<qc>/references/scope-and-safety.md` first,
then `<qc>/references/targets.md`, and the project's `.claude/qc-profile.md`
if it has one.

## HTTP service

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
python3 <qc>/scripts/probe.py --base "$BASE" --mode negative \
  --out "<scratchpad>/qc-<svc>-negative.json" [--only /api/<group>]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
```

`<scratchpad>` is the session's scratchpad or another temp directory. Probes:
wrong method, missing *required* query params, wrong type on typed params, and
(only with `--allow-writes`, only against a throwaway local DB) malformed
bodies on write routes. The token in `QC_TOKEN`, if exported, is sent as the
bearer token. `--fail-on-flags` gives exit 1 on any flagged result.

Then read the JSON. A finding is a `5xx`, an `exception-leak` flag, or a `2xx`
where invalid input should have been rejected. A correct 400/404/405/422 is a
pass: it goes in the coverage summary, not the findings.

## Code targets

Per the targets reference: run the existing suite first, then write short
temporary tests in a `qc_tmp` folder inside the project that feed missing
fields, wrong types, nulls, and malformed API responses to parsers,
validators, and stores. Delete them afterwards unless the user wants them
kept.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-negative.md`), worst first, with the
coverage summary. Offer to turn confirmed failures into tracked work.

## Common mistakes
- Calling a 401 a failure when no token was exported: that is auth working.
- Running `--allow-writes` against shared data.
