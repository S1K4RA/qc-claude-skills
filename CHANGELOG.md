# Changelog

Bump `version` in `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`
with every release, or `/plugin update` will not pick it up.

## 1.1.0 — 2026-10-23

- **Body probes**: write routes get schema-driven invalid bodies (empty object,
  each required field missing, each field mistyped, every field at and past
  its limits, random junk in fuzz), only with `--allow-writes`. Each result
  keeps the body it sent.
- **Scope mode** (`--mode scope`): the same requests as two users
  (`QC_TOKEN`, `QC_TOKEN_B`); flags `cross-user-access` on paths filled with
  user A's ids (`--value id=…`) and `same-data-both-users` on lists.
- **The spec is used fully**: `$ref`s and `allOf` resolved; min/max,
  maxLength, enum and format drive edge values; each route's `security`
  decides whether a 2xx without a token is a finding. Auth probes send
  required params so auth, not validation, answers.
- **New flags**: `accepted-invalid`, `timeout`, `no-response`, `slow`.
  Injection markers echoed inside JSON are a note, not `reflected-marker`.
  Leak detection matches stack-frame shapes instead of loose substrings.
- **summarize.py**: groups a run by endpoint × probe × flag with replay
  lines, per-endpoint status mix, missing security headers, notes and
  skipped endpoints; `--baseline` gives New / Fixed / Still failing and
  `--fail-on-new` makes it a regression gate.
- **Accepted findings**: `--accept FILE` on probe.py and summarize.py.
- `--value`, `--timeout`, `--slow-ms`, `--delay-ms`; `meta.cap_reached`.
- **serve.sh**: `--free-port` and `{port}` substitution.
- Hostile-input test templates for Dart/Flutter and Vitest/Jest.
- Plugin eval suite (`evals/`) and GitHub Actions CI for the unit tests.
- Shorter skill descriptions (less always-on context).

## 1.0.0 — 2026-10-23

- First release: `qc`, `qc-negative`, `qc-edge`, `qc-fuzz`, `qc-security`;
  `probe.py`, `serve.sh`, project profile, MOSA example.
