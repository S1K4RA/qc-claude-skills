---
name: qc-fuzz
description: >-
  Use when the user wants an endpoint, service, parser, or library they own
  hit with many random and malformed inputs to find crashes, 500s, or hangs,
  or says "fuzz", "throw random data at it", "property test the parser".
  Bounded correctness fuzzing, never load or DoS testing. Single bad inputs:
  qc-negative. Boundaries: qc-edge. Authz: qc-security. All: qc.
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
[[ -n "${BASE:-}" ]] || exit 1
# probe.py sends QC_TOKEN whenever it is set: for an unauthenticated run, `unset QC_TOKEN` first
python3 <qc>/scripts/probe.py --base "$BASE" --mode fuzz \
  --out "<scratchpad>/qc-<svc>-fuzz.json" --iterations 30 --max-requests 400 \
  [--only /api/<group>] [--seed 1337] [--allow-writes] [--delay-ms <ms>]
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>
python3 <qc>/scripts/summarize.py "<scratchpad>/qc-<svc>-fuzz.json"
```

Requests per endpoint = `--iterations`, until `--max-requests` (the hard cap)
runs out. `--seed` makes a run reproducible. The generator mixes random
strings, extreme numbers, injection/overflow/unicode markers, and null-ish
values across query params and path ids. With `--allow-writes` (local
throwaway DB only), write routes get the schema's valid body with random
fields replaced by junk of random types; each result keeps the body it sent.

Judge from the summary: any `server-error`, `exception-leak`, `timeout`, or
`no-response` is a finding. Record the replay line and the seed. Clean 4xx
across the random inputs is the pass.

## Code targets

Property-style, per the targets reference: copy the stack's template from
`<qc>/templates/` into a `qc_tmp` folder inside the project and fill its fuzz
group with the parsers and validators in scope, asserting each input parses
or throws a *handled* error, never crashes the process or test worker. Use
the project's property-testing library if it already has one (Go has native
`go test -fuzz`). Run the existing suite first; delete the temporary test
afterwards unless the user wants it kept.

## Report
One report via `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-fuzz.md`). Every finding carries its seed
and replay line. Offer to turn confirmed crashers into tracked work.

## Common mistakes
- Raising `--max-requests` to "fuzz harder": more iterations on a scoped
  `--only` beats volume, and volume is out of scope.
- Reporting a crash without its seed: it cannot be re-checked.
