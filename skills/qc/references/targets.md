# QC targets: HTTP services and code

Each QC type (negative, edge, fuzz, security) adapts to the kind of target.
An HTTP API is the richest target because it is a black-box surface the probe
engine can sweep. Client apps, libraries, and parsers are tested through the
project's own test runner plus focused review.

Paths below are relative to the `qc` skill's directory (`<qc>`): its base
directory when the `qc` skill is loaded, or `../qc` next to any focused skill.

## HTTP service, black box (primary path)

1. **Bring the service up and get its base URL.** Use the project profile's
   start command, through the helper so a service that is already running is
   reused and never stopped:
   ```bash
   eval "$(bash <qc>/scripts/serve.sh up --name <svc> --url http://localhost:<port> \
     --health <openapi-path> --start '<start command>' [--cwd <dir>])"   # sets BASE, STARTED
   ```
   `--health` must be a path only this service serves; a service is reused
   only when it answers 2xx there, and `up` fails if another program holds
   the port. Pass `--state-dir <scratchpad>` to keep its state and log out of
   `$TMPDIR`.
   Stop only what it started: `[[ "$STARTED" == 1 ]] && bash <qc>/scripts/serve.sh down --name <svc>`.
   If the project has its own start/stop wrapper that daemonises, pass it as
   `--start` and its stop command as `--stop`. For an authorised remote host,
   skip the helper, set `BASE` yourself, and pass `--allow-host`.
2. **Run the engine** for the mode(s) you want, writing JSON to the session's
   scratchpad (or another temp directory, never the repo):
   ```bash
   python3 <qc>/scripts/probe.py --base "$BASE" --mode <mode> \
     --out "<scratchpad>/qc-<svc>-<mode>.json" [--only /api/orders] \
     [--openapi <url-or-file>] [--allow-host <dev-host>] [--allow-writes] [--fail-on-flags]
   ```
   The OpenAPI document is auto-discovered at the common paths; without one,
   pass `--openapi <file>` or write an `--endpoints` list from the routes in
   code (format in `probe.py --help`). `--only` narrows to a path substring.
   The token in `QC_TOKEN` (or `--token-env <VAR>`) is sent as a bearer token.
   `--allow-writes` adds POST/PUT/PATCH/DELETE routes with garbage payloads
   meant to be rejected: only against a local throwaway database.
   `--fail-on-flags` turns flags into exit 1 for one-command gates.
3. **Read the JSON and judge.** The engine flags clear-cut problems
   (`server-error`, `exception-leak`, `reflected-marker`, `auth-not-enforced`),
   but findings come from comparing each result's `expected` to its
   `status` and `body_snippet`. A 400 where a 400 was expected is a pass.
4. **Scope by name.** When the user names a route group, an endpoint, or a
   project-specific endpoint ID, map it to a path prefix (the profile says
   where IDs are mapped) and pass `--only`.

GraphQL or RPC services: the engine does not speak them. Treat them as a code
target (below) and write temporary tests against the resolvers or handlers.

## Code target, through its test runner

For client apps, libraries, CLIs, and the server's own units: exercise parsing,
validation, and state logic with hostile inputs.

- **Baseline first**: run the existing suite, so new failures are yours.
- **Temporary tests live inside the project**, because runners only collect
  files under it and imports only resolve there. Use a `qc_tmp` folder in the
  test root, run just that folder, and delete it after reporting unless the
  user wants the tests kept (then move them next to the existing tests and
  name them like those).

| Stack | Runner | Temporary tests | Fuzz / property support |
|---|---|---|---|
| Dart / Flutter | `flutter test` / `dart test` | `test/qc_tmp/` | seeded `Random` loops |
| JS / TS | Vitest, Jest | `src/**/qc_tmp/` or `test/qc_tmp/` | `fast-check` if already a dependency, else seeded loops |
| Python | pytest | `tests/qc_tmp/` | `hypothesis` if installed, else seeded loops |
| .NET | `dotnet test` | `qc_tmp/` folder in the existing test project | FsCheck if referenced, else seeded loops |
| Java / Kotlin | JUnit (Gradle/Maven) | `src/test/.../qc_tmp/` | jqwik if present, else seeded loops |
| Go | `go test` | `qc_tmp_test.go` beside the package | native `go test -fuzz` |
| Rust | `cargo test` | `tests/qc_tmp.rs` | `proptest` if present, else seeded loops |

Do not add dependencies for a QC run. Use what the project already has.

- **negative / edge**: model deserialisers (`fromJson`, DTO mappers), form
  validators, repository and API-response parsing; feed missing fields, wrong
  types, nulls, empty lists, huge strings, bad dates, boundary numbers.
- **fuzz**: seeded random structures into parsers and validators; assert
  parse-or-handled-error, never an unhandled crash.
- **security**: token storage and transport, route / deep-link parameter
  rejection, output escaping (XSS markers escaped, not executed), no tokens or
  sensitive data in logs and error states. Read, don't exfiltrate; never touch
  secret config.
