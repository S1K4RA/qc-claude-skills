#!/usr/bin/env bash
# Copy the QC skills into a project or into your user skills folder, for when
# you don't want to install them as a plugin.
#
#   bash install.sh --project <dir>   # -> <dir>/.claude/skills/qc*
#   bash install.sh --user            # -> ~/.claude/skills/qc*
#
# Existing qc* skill folders at the destination are replaced. Re-run after a
# git pull to update.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills"

case "${1:-}" in
  --project) [[ -n "${2:-}" && -d "$2" ]] || { echo "usage: install.sh --project <existing dir>" >&2; exit 2; }
             DEST="$(cd "$2" && pwd)/.claude/skills";;
  --user)    DEST="$HOME/.claude/skills";;
  *)         sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2;;
esac

mkdir -p "$DEST"
for d in "$SRC"/qc*/; do
  name="$(basename "$d")"
  rm -rf "${DEST:?}/$name"
  cp -R "$d" "$DEST/$name"
  find "$DEST/$name" -name __pycache__ -type d -prune -exec rm -rf {} +
  echo "installed $name -> $DEST/$name"
done
echo "Restart Claude Code (or start a new session) to pick them up."
