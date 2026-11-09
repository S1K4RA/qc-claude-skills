---
name: qc
description: >-
  Use when the user wants an API, service, endpoint, app, or library they own
  checked for bugs across every kind of hostile input at once, or says "QC",
  "quality check", "find bugs in", "break", "harden", or "re-check the QC
  fixes", or wants a feature validated before release or before closing a
  change. For one kind only: qc-negative, qc-edge, qc-fuzz, qc-security.
---

# QC sweep (all four types)

Front door for quality-assurance testing of code the user's team owns. It
coordinates the four focused skills (`qc-negative`, `qc-edge`, `qc-fuzz`,
`qc-security`) so one command covers a target end to end; each also runs
standalone.

In this skill, `<qc>` is this skill's base directory: the scripts are in
`<qc>/scripts/`, the references in `<qc>/references/`, test templates in
`<qc>/templates/`.

**Read `<qc>/references/scope-and-safety.md` before probing.** It defines the
hard lines: local or authorised non-production targets only, non-destructive
by default, no load/DoS, never handle secrets.

## 1. Load the project profile

Read `.claude/qc-profile.md` (or `qc-profile.md`) in the project if it exists.
It names the targets, how to start them, the token variables, real ids for
`--value`, the accepted-findings file, allowed hosts, the request delay, the
report folder, and how findings are tracked. Without one, use the defaults in
`<qc>/references/project-profile.md` and discover the rest from the repo.

## 2. Pin down target and scope

- **Target**: an HTTP service (black-box probes) or code (the project's test
  runner). A named endpoint, route, or service means the HTTP target.
- **Scope**: whole service, one route group, a list of files, or a change
  (take the endpoints and files from its diff or task list). "QC this change"
  is the natural pre-release gate.
- **Environment**: a local service (default) or an authorised non-production
  host the user names.
- **Request delay**: the profile's delay for this host, else 0 locally. On a
  shared remote host without one, ask (suggest 100–200 ms). Pass it as
  `--delay-ms` to every probe run, including the subagents' runs.
- **Auth**: `probe.py` sends the token variable (`QC_TOKEN` by default)
  whenever it is set. For an unauthenticated run, `unset` it first;
  `meta.authenticated` shows which run you got. A second user's token
  (`QC_TOKEN_B`) unlocks the scope check in `qc-security`.
- **Writes**: body probes of POST/PUT/PATCH/DELETE run only with
  `--allow-writes`, against a local throwaway database. Ask before using it.

`<qc>/references/targets.md` says how each kind of target is tested per type.

## 3. Run the four types in parallel

They are independent, so run them concurrently:

- **With subagents** (preferred): dispatch four agents in one turn, one per
  focused skill, same target and scope; each returns its findings and the path
  to its raw results.
- **HTTP shortcut**: bring the service up once, then launch the probe modes
  together, each with its own `--out` in the session's scratchpad.
  `--max-requests` is a cap per process (150 below means 150 per mode).
  So is `--delay-ms`: four parallel processes each pause on their own, so
  the host sees four times the rate. When a delay is set, drop the `&` and
  run the modes one after another.
- **Without subagents**: run them in sequence; still one report.

`--health` must be a path only the target serves (its OpenAPI document is
ideal): `serve.sh` reuses a running service only when that path answers 2xx,
and refuses when a different program holds the port (`--free-port` picks
another and fills `{port}` in `--start`). If it exits non-zero, `BASE` stays
empty: read its message, and never probe a guessed URL.

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
[[ -n "${BASE:-}" ]] || exit 1
for m in negative edge fuzz security; do
  python3 <qc>/scripts/probe.py --base "$BASE" --mode $m [--accept <file>] \
    --out "<scratchpad>/qc-<svc>-$m.json" --max-requests 150 [--delay-ms <ms>] &
done; wait
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>   # only stop what qc started
python3 <qc>/scripts/summarize.py <scratchpad>/qc-<svc>-*.json [--accept <file>] \
  [--baseline <earlier run JSON>...]
```

## 4. Judge the results

Read the summary first; open the raw JSON only for the rows it points to. The
engine is black box; your judgement isn't. Read the target's source to decide
what each route is meant to do: auth attributes or middleware, validation,
whether it is public by design.

- A result is a finding when it contradicts that intent. The engine already
  takes auth from the spec's `security` (`auth` on each result); when a route
  has no spec auth and the code doesn't settle it, list it as
  **needs decision**, not as a bug.
- `timeout` and `no-response` are crash or hang candidates: check whether the
  service is still up before the next run.
- Quote the summary's replay line (URL and body) as the exact probe. Fuzz
  findings also need the run's seed.
- If the summary says the request cap was reached, endpoints went unprobed:
  narrow with `--only` and run again rather than raising the cap.
- Without `--allow-writes`, write routes get no body probes. Say so in the
  coverage summary rather than implying full coverage.
- **Re-checking fixes**: run the same modes, scope and seed, then pass the
  earlier JSON as `--baseline`; report New / Fixed / Still failing.

## 5. One consolidated report

Merge all findings into one report using `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-all.md`): worst first across types, stable
finding ids, and a no-issue coverage summary. Then offer to turn confirmed
failures into tracked work (regression test, spec scenario, or issue), and
offer accept rules for findings the team decides are by design. Do not write
either unasked.

## Common mistakes
- Stopping a service the user already had running: check `STARTED`.
- Treating every flag as a bug: a 2xx on a public route, or identical data
  for two users on shared reference data, is correct.
- Pointing at a remote host without `--allow-host`: the engine refuses, by
  design. Production is never in scope.
- Writing probe JSON into the repo, or leaving temporary tests behind. The
  report is the deliverable and goes in the report folder; the raw JSON stays
  in the scratchpad.
