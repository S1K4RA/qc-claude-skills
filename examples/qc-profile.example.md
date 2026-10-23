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
  start: `scripts/be.sh run <Svc>` (cwd: the hub); it daemonises and prints
  `http://localhost:<port>`, so pass it to serve.sh with
  `--stop 'scripts/be.sh stop <Svc>'`. `scripts/be.sh ps` lists what is up.
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
- Token variable: `PRODUCT_QC_TOKEN` (pass `--token-env PRODUCT_QC_TOKEN`),
  header `Authorization`, scheme `Bearer`. Copy it from a logged-in app or
  Postman session. Login decrypts credentials with keys from `appsettings`,
  so never mint one.
- Scope checks need two tokens: a sales user and an admin; dealer-scoped data
  needs users from two dealers.
- Known DEV data gap: some sales users have no supervisor, so their dealer
  scope is empty and a 403 is expected.

## Environments
- Allowed remote hosts for `--allow-host`: the team's DEV API host only.
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
