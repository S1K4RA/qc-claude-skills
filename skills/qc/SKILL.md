---
name: qc
description: >-
  Use when the user wants an API, service, endpoint, app, or library they own
  checked for bugs across every kind of hostile input at once — negative,
  edge-case, fuzz, and security — or says "QC", "quality check", "find bugs
  in", "break", "hammer", or "harden" a target, or wants a feature validated
  before release or before closing a change. For one kind only, use
  qc-negative, qc-edge, qc-fuzz, or qc-security instead.
---

# QC sweep (all four types)

Front door for quality-assurance testing of code the user's team owns. It
coordinates the four focused skills (`qc-negative`, `qc-edge`, `qc-fuzz`,
`qc-security`) so one command covers a target end to end; each also runs
standalone.

In this skill, `<qc>` is this skill's base directory: the scripts are in
`<qc>/scripts/`, the references in `<qc>/references/`.

**Read `<qc>/references/scope-and-safety.md` before probing.** It defines the
hard lines: local or authorised non-production targets only, non-destructive
by default, no load/DoS, never handle secrets.

## 1. Load the project profile

Read `.claude/qc-profile.md` (or `qc-profile.md`) in the project if it exists.
It names the targets, how to start them, the token variable, allowed hosts,
the report folder, and how findings are tracked. Without one, use the defaults
in `<qc>/references/project-profile.md` and discover the rest from the repo.

## 2. Pin down target and scope

- **Target**: an HTTP service (black-box probes) or code (the project's test
  runner). A named endpoint, route, or service means the HTTP target.
- **Scope**: whole service, one route group, a list of files, or a change
  (take the endpoints and files from its diff or task list). "QC this change"
  is the natural pre-release gate.
- **Environment**: a local service (default) or an authorised non-production
  host the user names.
- **Auth**: `probe.py` sends the token variable (`QC_TOKEN` by default)
  whenever it is set in the environment. For an unauthenticated run, `unset`
  it first; `meta.authenticated` in the JSON shows which run you got.

`<qc>/references/targets.md` says how each kind of target is tested per type.

## 3. Run the four types in parallel

They are independent, so run them concurrently:

- **With subagents** (preferred): dispatch four agents in one turn, one per
  focused skill, same target and scope; each returns its findings and the path
  to its raw results.
- **HTTP shortcut**: bring the service up once, then launch the four
  `probe.py` modes together. Give each a distinct `--out` in the session's
  scratchpad. `--max-requests` is a cap per process, so 150 here means 150
  per mode.
- **Without subagents**: run the four in sequence; still one report.

`--health` must be a path only the target serves (its OpenAPI document is
ideal): `serve.sh` reuses a running service only when that path answers 2xx,
and refuses when a different program holds the port. If it exits non-zero,
`BASE` stays empty: read its message, and never probe a guessed URL.

```bash
eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url <url> --health <openapi-path> --start '<cmd>')"  # BASE, STARTED
[[ -n "${BASE:-}" ]] || exit 1
for m in negative edge fuzz security; do
  python3 <qc>/scripts/probe.py --base "$BASE" --mode $m \
    --out "<scratchpad>/qc-<svc>-$m.json" --max-requests 150 &
done; wait
[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>   # only stop what qc started
```

## 4. Judge the results

The engine is black box; your judgement isn't. Read the target's source to
decide what each route is meant to do: auth attributes or middleware, its
validation, whether it is public by design.

- A result is a finding when it contradicts that intent: a flag, or a status
  that differs from `expected`. When intent is unclear (is this route meant to
  be public?), list it as **needs decision**, not as a bug.
- Missing security headers are not flagged; read `security_headers` on the
  `headers:baseline` results yourself.
- Quote each result's `url` as the exact probe, so the finding replays. Fuzz
  findings also need the run's seed (in `meta`).
- Without `--allow-writes`, negative mode on a read-only API covers little
  (wrong-type params and ids, missing required params). Say so in the
  coverage summary rather than implying full coverage.

## 5. One consolidated report

Merge all findings into one report using `<qc>/references/report-template.md`
(`<report folder>/<date>-<target>-all.md`): worst first across types, stable
finding ids, and a no-issue coverage summary. Then offer to turn confirmed
failures into tracked work (regression test, spec scenario, or issue, per the
template and profile). Do not write them unasked.

## Common mistakes
- Stopping a service the user already had running: check `STARTED`.
- Treating every flag as a bug: a 401 without a token, or a 200 on a public
  route, is correct. Judge each result against its `expected`.
- Pointing at a remote host without `--allow-host`: the engine refuses, by
  design. Production is never in scope.
- Writing probe JSON into the repo, or leaving temporary tests behind. The
  report is the deliverable and goes in the report folder; the raw JSON stays
  in the scratchpad.
