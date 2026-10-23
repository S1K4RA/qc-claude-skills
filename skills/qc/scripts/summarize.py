#!/usr/bin/env python3
"""Condense probe.py results into a short Markdown summary for judging and
for the report, optionally compared with an earlier run.

  summarize.py RUN.json [RUN2.json ...] [--baseline OLD.json ...] \
               [--accept accepted.json] [--examples 2] [--fail-on-new]

What it prints:
  - run facts per file (mode, requests, cap reached, auth, seed, skipped)
  - flag counts, then findings grouped by endpoint × probe × flag, each with
    an example URL (and request body) to replay and a body snippet
  - per-endpoint status histogram, so clean coverage is visible too
  - security headers missing per endpoint, and informational notes
  - with --baseline: new, fixed and still-failing findings

A finding's identity is (method, path, probe group, flag), where the probe
group drops the concrete value ("edge:query:page=2147483648" → "edge:query:page"),
so the same defect matches across runs and seeds. --fail-on-new exits 1 when
the run has a finding the baseline did not: a regression gate.
"""
import argparse
import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict

# Reuse probe.py's accept-rule matcher, loaded by path so this works however
# the script is invoked (python -I included).
_spec = importlib.util.spec_from_file_location(
    "qc_probe", os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe.py"))
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)


def group(label):
    return label.split("=", 1)[0]


def load(paths):
    runs = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            runs.append((p, json.load(f)))
    return runs


def findings(runs, rules):
    """{key: [result, ...]} for every live flag, after accept rules."""
    out = defaultdict(list)
    for _, run in runs:
        for r in run["results"]:
            for f in r["flags"]:
                ep = {"method": r["method"], "path": r["path"]}
                if any(probe.accepted(rule, ep, r["probe"], f) for rule in rules):
                    continue
                out[(r["method"], r["path"], group(r["probe"]), f)].append(r)
    return out


def missing_headers(runs, rules):
    out = {}
    for _, run in runs:
        for r in run["results"]:
            if not r.get("security_headers"):
                continue
            ep = {"method": r["method"], "path": r["path"]}
            gone = [h for h, v in r["security_headers"].items() if not v and not any(
                probe.accepted(rule, ep, r["probe"], f"missing-header:{h}") for rule in rules)]
            if gone:
                out[(r["method"], r["path"])] = gone
    return out


def short(text, n):
    text = (text or "").replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= n else text[:n] + "…"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="probe.py JSON results")
    ap.add_argument("--baseline", nargs="+", default=[], help="earlier results to compare with")
    ap.add_argument("--accept", help="JSON file of accepted findings (probe.py format)")
    ap.add_argument("--examples", type=int, default=1, help="example results per finding")
    ap.add_argument("--fail-on-new", action="store_true",
                    help="exit 1 when there is a finding the baseline did not have")
    args = ap.parse_args()

    rules = []
    if args.accept:
        with open(args.accept, encoding="utf-8") as f:
            rules = json.load(f)
    runs = load(args.runs)
    found = findings(runs, rules)
    lines = ["# QC run summary", ""]

    lines += ["## Runs", "", "| file | mode | requests | endpoints | cap reached | auth | writes | seed | flagged | accepted |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for path, run in runs:
        m = run["meta"]
        lines.append(f"| {os.path.basename(path)} | {m['mode']} | {m['requests']} | {m['endpoints']} | "
                     f"{'yes' if m.get('cap_reached') else 'no'} | {'yes' if m['authenticated'] else 'no'} | "
                     f"{'on' if m['allow_writes'] else 'off'} | {m.get('seed')} | {m['flagged']} | {m.get('accepted', 0)} |")
    skipped = [s for _, run in runs for s in run["meta"].get("skipped", [])]
    if skipped:
        lines += ["", "Skipped (pass `--value` for these): " + "; ".join(
            f"`{s['method']} {s['path']}` needs {', '.join(s['needs'])}" for s in skipped)]
    if any(run["meta"].get("cap_reached") for _, run in runs):
        lines += ["", "**The request cap was reached:** endpoints after it were not probed. "
                      "Narrow with `--only` rather than raising the cap."]

    counts = Counter(k[3] for k, rows in found.items() for _ in rows)
    lines += ["", "## Flags", ""]
    lines += [f"- `{f}`: {n} results" for f, n in counts.most_common()] or ["- none"]

    lines += ["", "## Findings (one per endpoint × probe × flag)", ""]
    if not found:
        lines += ["None flagged.", ""]
    for (method, path, grp, f), rows in sorted(found.items(), key=lambda kv: (kv[0][3], kv[0][1])):
        statuses = Counter(str(r["status"]) for r in rows)
        lines.append(f"### `{f}` — `{method} {path}` — {grp} ({len(rows)}×; status "
                     + ", ".join(f"{s}×{n}" for s, n in statuses.most_common()) + ")")
        for r in rows[:args.examples]:
            lines.append(f"- replay: `{method} {short(r['url'], 300)}`")
            if r.get("request_body"):
                lines.append(f"  body: `{short(r['request_body'], 300)}`")
            if r.get("status_a") is not None:
                lines.append(f"  user A status: {r['status_a']}; same body: {r.get('same_body')}")
            lines.append(f"  expected: {r['expected']}; got: `{short(r['body_snippet'] or r.get('error'), 200)}`")
        lines.append("")

    lines += ["## Coverage by endpoint", "", "| endpoint | requests | statuses | flags |", "|---|---|---|---|"]
    per = defaultdict(list)
    for _, run in runs:
        for r in run["results"]:
            per[(r["method"], r["path"])].append(r)
    for (method, path), rows in sorted(per.items(), key=lambda kv: kv[0][1]):
        st = Counter(str(r["status"]) for r in rows)
        fl = Counter(f for (m, p, _, f), rs in found.items() if (m, p) == (method, path) for _ in rs)
        lines.append(f"| `{method} {path}` | {len(rows)} | "
                     + ", ".join(f"{s}×{n}" for s, n in st.most_common()) + " | "
                     + (", ".join(f"{f}×{n}" for f, n in fl.most_common()) or "—") + " |")

    gone = missing_headers(runs, rules)
    if gone:
        lines += ["", "## Missing security headers", ""]
        common = set.intersection(*(set(v) for v in gone.values()))
        if common and len(gone) > 1:
            lines.append(f"- on all {len(gone)} probed endpoints: {', '.join(sorted(common))}")
        for (method, path), hs in sorted(gone.items(), key=lambda kv: kv[0][1]):
            rest = [h for h in hs if h not in common or len(gone) == 1]
            if rest:
                lines.append(f"- `{method} {path}`: {', '.join(rest)}")

    notes = Counter((r["method"], r["path"], n) for _, run in runs for r in run["results"]
                    for n in r.get("notes", []))
    if notes:
        lines += ["", "## Notes (not findings on their own)", ""]
        lines += [f"- `{m} {p}`: {n} ({c}×)" for (m, p, n), c in notes.most_common()]

    new = []
    if args.baseline:
        before = findings(load(args.baseline), rules)
        new = sorted(set(found) - set(before))
        fixed = sorted(set(before) - set(found))
        still = sorted(set(found) & set(before))
        lines += ["", "## Compared with baseline", ""]
        for title, keys in (("New", new), ("Fixed", fixed), ("Still failing", still)):
            lines.append(f"**{title}** ({len(keys)})")
            lines += [f"- `{f}` — `{m} {p}` — {g}" for m, p, g, f in keys] or ["- none"]
            lines.append("")
        lines.append("A key absent from both runs may simply not have been probed: "
                     "compare runs with the same mode, scope and seed.")

    print("\n".join(lines))
    sys.exit(1 if (args.fail_on_new and new) else 0)


if __name__ == "__main__":
    main()
