# QC scope and safety

These skills are **quality-assurance testing of code the user's team owns**.
The point is to find input-validation, boundary, and authorization defects
before users do. That is ordinary QC. Keep it that way by staying inside these
lines.

## Allowed targets
- A service **started locally** for the run (the normal case), via the
  project's own start command or `scripts/serve.sh up`.
- An **authorised non-production environment** (DEV, staging) that the user
  names explicitly, or that the project profile lists under allowed hosts. Pass
  its host with `--allow-host <host>` so `probe.py` accepts it. The engine
  refuses every other host by default.

Never point these at production, at a host the user has not authorised, or at
any third party. If you are unsure whether a target is in scope, ask.

## Non-destructive by default
- Probes are **read-only** unless the user passes `--allow-writes`. Even then,
  write-method bodies are invalid variants meant to be *rejected* (malformed
  JSON, missing or mistyped fields, out-of-range values), never a plain valid
  record. But a target that wrongly accepts one has written it: that is the
  finding, and it is also a write. So `--allow-writes` is for a local service
  with a throwaway database only, and needs the user's yes.
- There is a global request cap (`--max-requests`), a per-request timeout,
  and an optional pause between requests (`--delay-ms`, use it on shared DEV).
  This is correctness testing, not load or stress testing. Do **not** remove the
  cap, loop the engine to generate volume, or run it to measure throughput.
  Denial-of-service and load testing are out of scope for these skills.

## Secrets
- Never read, print, or edit secret or config-secret files: `.env*`,
  `appsettings*.json`, `config.*.json`, `secrets.*`, `credentials*`, keystores
  and keys (`*.jks`, `*.keystore`, `*.p12`, `*.pfx`, `*.pem`, `*.key`). The
  project profile or the project's own instructions may list more. Shell
  commands count too: no `cat`, `grep`, or `jq` on them.
- These skills **never mint their own tokens** (that would need signing keys
  from those files). For authenticated probes the user supplies a token in the
  environment, by default `QC_TOKEN` (the profile may name another variable),
  for example copied from a logged-in app or an API client. The engine sends it
  as a bearer token. Without one, protected routes mostly return 401, which is
  itself a useful signal that auth is enforced. Scope checks use a second
  user's token (`QC_TOKEN_B`) the same way; ask for two ordinary test users,
  never a real customer's session.

## What "security" covers here
Authorization enforcement (missing / invalid / forged-unsigned / expired
tokens), whether one test user can read another test user's records, input validation of injection **markers** (confirming the API rejects
or sanitises them rather than reflecting them or 500-ing), security response
headers, and information leakage (stack traces, SQL errors in responses). These
are standard defensive checks. The skills do not build working exploits, chain
vulnerabilities, or attempt to exfiltrate data. A flagged marker is a *signal
to fix validation*, which the report hands to the team.
