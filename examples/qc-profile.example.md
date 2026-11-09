# QC profile: example multi-repo product

A filled-in profile for a product built from three repos: a .NET API made of
several services, a Flutter mobile app, and a Vue web CMS, coordinated from a
hub repo that holds specs. Copy it to `.claude/qc-profile.md` in your project
and replace what differs.

## Targets
| key | kind | where | notes |
|---|---|---|---|
| be | http | ../product-be | default when the user names a service, an endpoint, or an endpoint ID |
| app | code | ../product-app | Flutter, tests through fvm |
| cms | code | ../product-cms | Vue 3 + Vitest |

## Bring up an HTTP target
- be: one service per run, named by its short name (Orders, Auth, Catalog).
  start: `scripts/be.sh run <Svc> --port {port}` (cwd: the hub); it
  daemonises, so pass `--stop 'scripts/be.sh stop <Svc>'` too, and
  `--free-port` when the default port is taken. `scripts/be.sh ps` lists what
  is up.
  health: `/swagger/v1/swagger.json`.
- Endpoint IDs look like `AB012`. Map an ID to its route with the backend's
  `Documentation/API_ENDPOINT_IDS.md`, then pass the route prefix to `--only`.

## Run code targets
- app: test `scripts/app.sh test [path]`; temporary tests in `test/qc_tmp/`
  (run `scripts/app.sh test test/qc_tmp`); keep-worthy tests move to the
  matching `test/` subfolder.
- cms: test `scripts/cms.sh test`; temporary tests in `src/tests/qc_tmp/`;
  keep-worthy tests move to `src/tests/unit/`.

## Auth
- Token variables: `PRODUCT_QC_TOKEN` and `PRODUCT_QC_TOKEN_B` (pass
  `--token-env PRODUCT_QC_TOKEN --token-env-b PRODUCT_QC_TOKEN_B`), header
  `Authorization`, scheme `Bearer`. Copy them from logged-in app or Postman
  sessions. Login decrypts credentials with keys from `appsettings`, so never
  mint one.
- Scope checks: user A and user B are sales users of two different dealers.
  Ask for one of A's order ids and pass it as `--value id=<id>`.
- Known DEV data gap: some sales users have no supervisor, so their dealer
  scope is empty and a 403 is expected.

## Accepted findings
- `.claude/qc-accepted.json`, passed as `--accept` to probe.py and
  summarize.py. Holds the public-by-design routes (health, app version) and
  the headers the API gateway adds in DEV.

## Environments
- Allowed remote hosts for `--allow-host`: the team's DEV API host only.
- Request delay: 200 ms on the DEV API host (shared with the app team);
  0 against a local service.
- Never: production or staging.

## Secrets (never read)
`appsettings*.json`, `config.*.json` (app), `google-services.json`,
`GoogleService-Info.plist`, `key.properties`, `local.properties`.

## Reports
- Folder: `docs/test-scenarios/qc/` in the hub.
- Frontmatter:
  ```yaml
  ---
  id: qc-<target>-<type>-<date>
  title: QC <type> — <target/scope>
  repos: [be]
  endpoints: [AB012]
  status: active
  source: hand-written
  last_verified: <date>
  ---
  ```
- Findings tracking: each confirmed failure becomes an OpenSpec scenario in
  the relevant capability's delta spec, named
  `QC <type> <scope> F<k>: <endpoint> rejects <bad input>`. Deferred findings
  go in the change's traceability table, never as a SHALL scenario.
