---
name: qc-edge
description: >-
  Use when the user wants an API endpoint or service, an app, or a library
  they own checked at the limits of its inputs — empty, zero, negative, very
  large, min/max, off-by-one, unicode and emoji, whitespace-only, null-ish, bad
  dates, pagination extremes — or says "edge cases", "boundary test", "corner
  cases", "what about empty / huge / negative input", "test the limits of this
  endpoint". Plain invalid input: qc-negative. Random junk: qc-fuzz. Security:
  qc-security. All four at once: qc.
---

# Edge-case testing

Pushes a target to the **boundaries of its input space**, where validation and
arithmetic slip: empty, zero, negative, overflow, huge strings, unicode,
whitespace, null-ish, bad dates. Correct behaviour at the extremes is a
sensible 2xx (empty or clamped data) or a clean 400, never a 500 or a hang.

`<qc>` is the `qc` skill's directory, next to this one (`../qc` from this
skill's base directory). Read `<qc>/references/scope-and-safety.md` and
`<qc>/references/targets.md` first, and the project's `.claude/qc-profile.md`
if it has one.

## HTTP service

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
python3 <qc>/scripts/probe.py --base "$BASE" --mode edge \
  --out "<scratchpad>/qc-<svc>-edge.json" [--only /api/<group>]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
```

The engine sweeps boundary values through each query param and templated path
id (empty, 0, -1, 2^31 and 2^63 over/underflow, 20k-char strings, unicode, bad
dates). Write routes are only included with `--allow-writes`, and only against
a throwaway local DB. The token in `QC_TOKEN`, if exported, is sent as the
bearer token; `--fail-on-flags` gives exit 1 on any flag.

Judge the JSON with attention to pagination (`page=0`, huge `page`), ids (`0`,
`-1`, overflow) and date params. A `server-error` or `exception-leak` flag is a
finding; a handled boundary is coverage.

## Code targets

Per the targets reference: run the existing suite first, then write short
temporary tests in a `qc_tmp` folder inside the project that feed boundary
values into model parsing, validators, numeric and date fields, and list
rendering. Delete them afterwards unless the user wants them kept.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-edge.md`), worst first, with the coverage
summary. Offer to turn confirmed failures into tracked work.

## Common mistakes
- Reporting an empty-result 200 for `page=999999` as a bug: that is handled.
- Forgetting overflow ids are also path params, not just query params.
