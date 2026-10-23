---
name: qc-edge
description: >-
  Use when the user wants an endpoint, service, app, or library they own
  checked at the limits of its inputs — empty, zero, negative, min/max,
  off-by-one, huge, unicode, whitespace, null-ish, bad dates, pagination
  extremes — or says "edge cases", "boundary test", "corner cases". Invalid
  input: qc-negative. Random junk: qc-fuzz. Security: qc-security. All: qc.
---

# Edge-case testing

Pushes a target to the **boundaries of its input space**, where validation and
arithmetic slip. Correct behaviour at the extremes is a sensible 2xx (empty or
clamped data) or a clean 400, never a 500 or a hang.

`<qc>` is the `qc` skill's directory, next to this one (`../qc` from this
skill's base directory). Read `<qc>/references/scope-and-safety.md` and
`<qc>/references/targets.md` first, and the project's `.claude/qc-profile.md`
if it has one.

## HTTP service

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
[[ -n "${BASE:-}" ]] || exit 1
# probe.py sends QC_TOKEN whenever it is set: for an unauthenticated run, `unset QC_TOKEN` first
python3 <qc>/scripts/probe.py --base "$BASE" --mode edge \
  --out "<scratchpad>/qc-<svc>-edge.json" [--only /api/<group>] [--allow-writes] [--value id=<real id>]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
python3 <qc>/scripts/summarize.py "<scratchpad>/qc-<svc>-edge.json"
```

Values come from the schema first: `min-1`/`min`, `max`/`max+1`,
`maxLength`/`maxLength+1`, a value outside the `enum`, a malformed `date`,
`uuid`, or `email`. Then generic ones: empty, 0, -1, 2^31 and 2^63 overflow,
20k-char strings, unicode, whitespace. A schema-forbidden value that gets a 2xx
is flagged `accepted-invalid`; the last allowed value should succeed. With
`--allow-writes` (local throwaway DB only), every body field of a write route
gets the same treatment, plus `null` for required fields. `--value` fills
other path params with real ids so the probe reaches the handler.

Judge with attention to pagination (`page=0`, huge `page`), ids, and dates. A
`server-error`, `exception-leak`, `accepted-invalid`, or `timeout` is a
finding; a handled boundary is coverage.

## Code targets

Per the targets reference: run the existing suite first, then copy the
stack's template from `<qc>/templates/` into a `qc_tmp` folder inside the
project and fill its edge group with the boundary values of the model
parsing, validators, numeric and date fields, and list rendering in scope.
Delete it afterwards unless the user wants the tests kept.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-edge.md`), worst first, with the coverage
summary. Offer to turn confirmed failures into tracked work.

## Common mistakes
- Reporting an empty-result 200 for `page=999999` as a bug: that is handled.
- Forgetting overflow ids are also path params, not just query params.
