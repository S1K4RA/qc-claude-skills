# QC project profile

The skills are project-agnostic. Everything that differs per project lives in
one optional file in the project: **`.claude/qc-profile.md`** (also accepted:
`qc-profile.md` at the project root). Read it first when it exists; it
overrides the defaults below.

## When there is no profile

Work from defaults and what the repo shows:

| Setting | Default / how to discover |
|---|---|
| Targets | HTTP services: look for an OpenAPI document, a server entry point, a `docker-compose.yml`, `package.json` scripts, `launchSettings.json`. Code targets: the test runner the repo already uses. |
| Start / stop | The repo's own run command (README, `Makefile`, `package.json`, `scripts/`), wrapped by `scripts/serve.sh`. |
| Base URL and health path | From launch config or the start command's output; health = the OpenAPI path, or another route only this service serves. If the default port is taken by something else, pick a free one. |
| OpenAPI | Auto-discovered by `probe.py` (common paths); else `--openapi` or an `--endpoints` list you write from the routes in code. |
| Token variable | `QC_TOKEN`, sent as `Authorization: Bearer <token>`. |
| Allowed remote hosts | None. Local only unless the user names a host. |
| Report folder | `qc-reports/` in the project (the team decides whether to commit or ignore it), no frontmatter. |
| Findings tracking | Offer a regression test per confirmed finding. |

After the first run, offer to save what you learned as a profile so the next
run starts straight away. Write it only when the user says yes, and never put
secret values in it (variable *names* only).

## Profile shape

Plain Markdown, so both people and agents can read it. Use these headings;
leave out any that do not apply.

```markdown
# QC profile: <project>

## Targets
| key | kind | where | notes |
|---|---|---|---|
| api | http | ./server | the default target for endpoint / route names |
| web | code | ./web | Vue 3 + Vitest |

## Bring up an HTTP target
- api: url http://localhost:5040, health /swagger/v1/swagger.json
  start: `dotnet run --project src/Api` (cwd ./server)
  stop: (none: serve.sh kills what it started)
- Endpoint scoping: route prefixes per group, or an ID scheme and where it is
  mapped to routes (e.g. "AK012 ids, see docs/endpoint-ids.md").

## Run code targets
- web: test `npm run test`; temporary tests in `src/tests/qc_tmp/`;
  keep-worthy tests move to `src/tests/unit/`.

## Auth
- Token variable: `QC_TOKEN` (header `Authorization`, scheme `Bearer`).
- Roles worth a second token for scope checks: admin vs. regular user.
- Known data gaps that make a 403 expected, not a bug.

## Environments
- Allowed remote hosts for `--allow-host`: dev.example.internal
- Never: production hosts.

## Secrets (never read)
Extra files beyond the defaults in scope-and-safety.md.

## Reports
- Folder: `docs/qc/`
- Frontmatter to start each report with (copy literally, fill the fields).
- Findings tracking: how a confirmed finding becomes a spec scenario, issue,
  or regression test, and the naming convention for it.
```

`examples/qc-profile.example.md` in the skills repository is a filled-in
profile for a multi-repo product (API + mobile app + web CMS).
