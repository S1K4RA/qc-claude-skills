#!/usr/bin/env bash
# Bring up a local HTTP target for QC and print shell assignments on stdout
# for the caller to eval:
#   BASE=http://localhost:<port>
#   STARTED=0|1     1 when this script started the service (stop it afterwards
#                   with `serve.sh down --name <name>`); 0 when it reused one
#                   that was already answering (leave that one alone)
# Everything else goes to stderr.
#
#   eval "$(bash serve.sh up --name api --url http://localhost:5040 \
#            --health /openapi.json [--expect '"openapi"'] \
#            [--start '<command>'] [--stop '<command>'] [--cwd <dir>] \
#            [--free-port] [--timeout 120] [--state-dir <dir>])"
#   bash serve.sh down --name api [--state-dir <dir>]
#
# Pick a --health path only your service serves (its OpenAPI document is
# ideal). A service is reused only when that path returns 2xx/3xx (and its
# body contains --expect, if given). Anything else answering on the port is a
# different program: up fails instead of probing the wrong target, unless
# --free-port is given: then it picks a free port, puts it in the URL, and
# substitutes it for {port} in --start and --stop.
#
# --start runs in the background in its own process group; `down` kills that
# group. When the project's own start command daemonises itself (returns at
# once), pass --stop with the project's stop command and `down` runs that
# instead. It only ever serves a *local* target. For an authorised remote host,
# skip this, set BASE yourself, and pass --allow-host to probe.py.
set -euo pipefail

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
[[ $# -ge 1 ]] || usage
cmd="$1"; shift
NAME="" URL="" START="" STOP="" CWD="$PWD" HEALTH="/" EXPECT="" TIMEOUT=120 FREE=0
STATE_DIR="${QC_STATE_DIR:-${TMPDIR:-/tmp}}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2;;
    --url) URL="${2%/}"; shift 2;;
    --start) START="$2"; shift 2;;
    --stop) STOP="$2"; shift 2;;
    --cwd) CWD="$2"; shift 2;;
    --health) HEALTH="$2"; shift 2;;
    --expect) EXPECT="$2"; shift 2;;
    --state-dir) STATE_DIR="$2"; shift 2;;
    --timeout) TIMEOUT="$2"; shift 2;;
    --free-port) FREE=1; shift;;
    -h|--help|help) usage;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[[ -n "$NAME" ]] || { echo "--name is required" >&2; exit 2; }
mkdir -p "$STATE_DIR"
STATE="${STATE_DIR%/}/qc-serve-${NAME//[^A-Za-z0-9_-]/_}"

# Sets CODE to the health path's HTTP status ("000" when nothing answers) and
# MATCH to 1 when the body carries the --expect marker (or none was given).
CODE="000" MATCH=1
check() {
  local body
  body=$(curl -sk -m 3 -w $'\n%{http_code}' "$URL$HEALTH" 2>/dev/null) || true
  CODE="${body##*$'\n'}"; [[ -n "$CODE" ]] || CODE="000"
  MATCH=1
  [[ -z "$EXPECT" || "$body" == *"$EXPECT"* ]] || MATCH=0
}
healthy() { check; [[ "$CODE" =~ ^[23] && "$MATCH" == 1 ]]; }

case "$cmd" in
  up)
    [[ -n "$URL" ]] || { echo "--url is required for up" >&2; exit 2; }
    if healthy; then
      echo "reusing $NAME already serving $URL$HEALTH (not started by qc; it will not be stopped)" >&2
      [[ -z "$START" ]] || echo "(--start not run: the service was already up)" >&2
      printf 'BASE=%s\nSTARTED=0\n' "$URL"; exit 0
    fi
    if [[ "$CODE" != "000" ]]; then
      echo "something else answers at $URL (HTTP $CODE on $HEALTH${EXPECT:+, or body lacks '$EXPECT'}): not $NAME." >&2
      if [[ "$FREE" != 1 || "$START" != *"{port}"* ]]; then
        echo "Pick a free port, pass --free-port with {port} in --start, or stop that program yourself." >&2
        exit 1
      fi
      port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
      URL=$(printf '%s' "$URL" | sed -E "s#:[0-9]+\$#:$port#")
      echo "using free port $port instead" >&2
    fi
    port=$(printf '%s' "$URL" | sed -nE 's#.*:([0-9]+)$#\1#p')
    START="${START//\{port\}/$port}"; STOP="${STOP//\{port\}/$port}"
    [[ -n "$START" ]] || { echo "$URL is not answering and no --start command was given" >&2; exit 1; }
    echo "starting $NAME: $START (log: $STATE.log)" >&2
    set -m  # background job gets its own process group, so down can kill the tree
    (cd "$CWD" && exec bash -c "$START") >"$STATE.log" 2>&1 &
    pid=$!
    set +m
    printf 'PID=%q\nSTOP=%q\nCWD=%q\n' "$pid" "$STOP" "$CWD" >"$STATE.state"
    for ((i = 0; i < TIMEOUT; i++)); do
      if healthy; then
        printf 'BASE=%s\nSTARTED=1\n' "$URL"
        echo "# started by qc: stop it when done with: bash serve.sh down --name $NAME" >&2
        exit 0
      fi
      if [[ -z "$STOP" ]] && ! kill -0 "$pid" 2>/dev/null; then
        echo "$NAME exited before answering; last log lines:" >&2
        tail -n 20 "$STATE.log" >&2; rm -f "$STATE.state"; exit 1
      fi
      sleep 1
    done
    echo "$NAME did not answer at $URL$HEALTH within ${TIMEOUT}s; log: $STATE.log" >&2
    exit 1;;
  down)
    [[ -f "$STATE.state" ]] || { echo "nothing started by qc under name $NAME" >&2; exit 0; }
    # shellcheck disable=SC1090
    source "$STATE.state"
    if [[ -n "$STOP" ]]; then
      (cd "$CWD" && bash -c "$STOP") >&2 || true
    fi
    kill -TERM -- "-$PID" 2>/dev/null || kill -TERM "$PID" 2>/dev/null || true
    rm -f "$STATE.state" "$STATE.log"
    echo "stopped $NAME" >&2;;
  *) usage;;
esac
