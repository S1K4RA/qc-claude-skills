# QC report format

Every QC run writes one Markdown report to the report folder (project profile,
default `qc-reports/`), named `YYYY-MM-DD-<target>-<type>.md`, where `<type>`
is `negative`, `edge`, `fuzz`, `security`, or `all` for a full sweep (e.g.
`2026-10-21-api-orders-security.md`). If the profile gives frontmatter, start
the report with it.

```markdown
# QC <type> — <scope>

One line: what was probed, which environment, with or without auth.

## Run facts
- Target: <service> on <base url> (local service you started | authorised <env>)
- Mode(s): negative | edge | fuzz | security
- Requests: <n> over <m> endpoints · authenticated: yes/no · writes: off/on · seed: <n>
- Raw results: <path to the probe JSON or test output>
- Summary: `summarize.py` output (and the baseline it was compared with, if any)

## Findings
Ranked worst first. One subsection per finding, with a stable finding id.

### F1 — <short title> (<severity: high|med|low>)
- Endpoint / unit: `GET /api/...` or `lib/.../parser.dart` (project ID if any)
- Probe: <probe label and the result's exact `url`, e.g. security:injection-marker:q → `GET /api/search?q=%27+OR+%271%27%3D%271`>
- Observed: <status, flag, body snippet — the evidence>
- Expected: <what a correct target should have done>
- Why it matters: <impact in one or two sentences>

## No-issue summary
What was probed and came back clean, so a later reader knows the coverage
(the summary's "Coverage by endpoint" table), and what was *not* probed:
write routes without `--allow-writes`, scope routes skipped for lack of ids,
endpoints past the request cap.

## Re-check (when compared with a baseline)
New / Fixed / Still failing, by finding id.
```

Severity guide: **high** for auth not enforced, cross-user access, role or
scope bypass, data from another tenant, stack traces or SQL errors to clients, crashes on plausible
input. **med** for 500s on malformed input, reflected markers, accepted-invalid
input, timeouts. **low** for missing security headers, unhelpful error
messages, inconsistent status codes.

## Turning findings into tracked work

Every confirmed failure should become something a later agent can re-check.
Follow the profile's convention if it has one; otherwise offer, in this order:

1. **A regression test** in the project's suite that fails today and passes
   once fixed (move the temporary QC test into place, or write one for an HTTP
   finding in the project's integration-test style).
2. **A spec scenario**, when the project keeps specs (OpenSpec, Gherkin
   `.feature` files, a test plan): name it after the finding, e.g.
   `QC <type> <scope> F<k>: <endpoint> rejects <bad input>`, with the probe as
   the WHEN and the correct behaviour as the THEN. A finding the team decides
   not to fix yet is recorded as deferred, never as a requirement the code
   already fails.
3. **An issue** in the project's tracker, with the report section as the body.

Offer; do not write specs, tests, or issues unasked.
