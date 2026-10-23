---
name: qc-fuzz
description: >-
  Use when the user wants an API endpoint or service, an app's parsers, or a
  library they own bombarded with many randomised and malformed inputs to find
  crashes, 500s, or hangs — or says "fuzz", "fuzz test", "throw random data at
  it", "hammer this endpoint with junk", "property test the parser". This is
  bounded correctness fuzzing, never load or denial-of-service testing. Single
  invalid inputs: qc-negative. Named boundaries: qc-edge. Authz and injection:
  qc-security. All four at once: qc.
---

# Fuzz testing

Feeds **many randomised, malformed, and oversized inputs** to find the one the
developer never imagined: the unhandled exception, the 500, the hang. A robust
target processes the input or rejects it cleanly; it never crashes.

This is **bounded correctness fuzzing**, not load testing. `<qc>` is the `qc`
skill's directory, next to this one (`../qc` from this skill's base
directory). Read `<qc>/references/scope-and-safety.md` first: the request cap
stays on and the engine is never used to generate volume or measure
throughput. Then `<qc>/references/targets.md`, and the project's
`.claude/qc-profile.md` if it has one.

## HTTP service

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
python3 <qc>/scripts/probe.py --base "$BASE" --mode fuzz \
  --out "<scratchpad>/qc-<svc>-fuzz.json" --iterations 30 --max-requests 400 \
  [--only /api/<group>] [--seed 1337]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
```

`--iterations` is randomised requests per endpoint, `--seed` makes a run
reproducible, `--max-requests` is the hard cap. The generator mixes random
strings, extreme numbers, injection/overflow/unicode markers, and empty or
null-ish values across query params and path ids. Write routes are only
included with `--allow-writes`, against a throwaway local DB. The token in
`QC_TOKEN`, if exported, is sent as the bearer token; `--fail-on-flags` gives
exit 1.

Judge the JSON: any `server-error` or `exception-leak` flag is a finding.
Record the triggering request and the seed so it reproduces. Clean 4xx across
the random inputs is the pass.

## Code targets

Property-style, per the targets reference: write a short temporary test in a
`qc_tmp` folder inside the project that generates many seeded random inputs
and feeds them to parsers and validators, asserting each parses or throws a
*handled* error, never crashes the process or test worker. Use the project's
property-testing library if it already has one (Go has native `go test
-fuzz`); otherwise a seeded loop. Run the existing suite first; delete the
temporary test afterwards unless the user wants it kept.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-fuzz.md`). Every finding carries its seed
and triggering input. Offer to turn confirmed crashers into tracked work.

## Common mistakes
- Raising `--max-requests` to "fuzz harder": more iterations on a scoped
  `--only` beats volume, and volume is out of scope.
- Reporting a crash without its seed: it cannot be re-checked.
