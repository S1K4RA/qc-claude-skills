# qc-claude-skills

Claude Code skills for **quality-assurance testing of code you own**: the
negative, edge-case, fuzz, and security checks a QC engineer would run, in one
command or one kind at a time.

| Skill | Use it for |
|---|---|
| `qc` | All four kinds at once, one consolidated report |
| `qc-negative` | Invalid input: wrong method, missing fields, wrong types, malformed JSON |
| `qc-edge` | Boundaries: empty, zero, negative, overflow, huge strings, unicode, bad dates, pagination extremes |
| `qc-fuzz` | Many seeded random and malformed inputs, looking for crashes, 500s, and hangs |
| `qc-security` | Auth enforcement, injection markers, error leakage, security headers |

They work on two kinds of target:

- **HTTP APIs**, black box: `probe.py` reads the OpenAPI document (it finds
  it at the usual paths) and sends a bounded, read-only set of probes.
- **App and library code**, through the project's own test runner: temporary
  tests in a `qc_tmp` folder, deleted afterwards. Supported for Dart/Flutter,
  JS/TS, Python, .NET, JVM, Go, and Rust.

Say "QC the orders API", "fuzz the invoice parser", or "is /api/admin
protected?" and the matching skill takes over.

## Install

**As a plugin** (recommended; updates with `/plugin update`):

```
/plugin marketplace add S1K4RA/qc-claude-skills
/plugin install qc@qc-claude-skills
```

The repository is private, so your `git` needs access to it (for example,
`gh auth login`).

**As plain skills**, copied into one project or into your user folder:

```bash
git clone https://github.com/S1K4RA/qc-claude-skills.git
bash qc-claude-skills/install.sh --project /path/to/project   # -> .claude/skills/
bash qc-claude-skills/install.sh --user                       # -> ~/.claude/skills/
```

Restart Claude Code afterwards.

## Per-project setup: the QC profile

The skills contain nothing project-specific. Anything that differs per
project goes in an optional `.claude/qc-profile.md` in that project: the
targets, how to start them, the token variable, allowed DEV hosts, the report
folder and its frontmatter, and how findings are tracked (regression tests,
OpenSpec scenarios, issues).

Without a profile, the skills use defaults (`QC_TOKEN`, local targets only,
reports in `qc-reports/`) and work out the rest from the repo. After the
first run they offer to save what they learned as a profile. The format is in
[`skills/qc/references/project-profile.md`](skills/qc/references/project-profile.md),
and [`examples/qc-profile.example.md`](examples/qc-profile.example.md) is a
filled-in profile for a multi-repo product.

## Safety lines

These rules are enforced in the engine, not only written in the docs:

- **Local or authorised hosts only.** `probe.py` refuses any host other than
  localhost unless you pass it with `--allow-host`. Production is never in
  scope.
- **Read-only by default.** Write methods are probed only with
  `--allow-writes`, and then only with garbage payloads meant to be rejected.
  Use it only against a throwaway database.
- **Bounded.** A request cap and a per-request timeout apply. This is
  correctness testing, not load or DoS testing.
- **No secrets.** The skills never read secret or config files and never mint
  tokens. For authenticated probes you export a token yourself
  (`export QC_TOKEN=...`).

Full text: [`skills/qc/references/scope-and-safety.md`](skills/qc/references/scope-and-safety.md).

## Running the engine by hand

```bash
python3 skills/qc/scripts/probe.py --help
eval "$(bash skills/qc/scripts/serve.sh up --name api --url http://localhost:8000 \
  --start 'uvicorn app:app --port 8000' --health /openapi.json)"
python3 skills/qc/scripts/probe.py --base "$BASE" --mode security --out /tmp/qc-security.json
[[ "$STARTED" == 1 ]] && bash skills/qc/scripts/serve.sh down --name api
```

Python 3.8+ (stdlib only), bash, and curl.

## Development

```bash
python3 -m unittest discover -s tests -v
```

`tests/mock_api.py` is a deliberately buggy API. The tests check that each
mode flags its planted defect, that the request cap and seed hold, that
remote hosts are refused, and that `serve.sh` stops only what it started.
