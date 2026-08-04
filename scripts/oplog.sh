#!/usr/bin/env bash
# Append one operational event to log/ops_log.jsonl (append-only, committed).
# For routine-level events the engine cannot record: market-closed days, run
# failures, halts/resumes, manual interventions. See AUDIT.md.
#
# Usage: bash scripts/oplog.sh <event> "<one-line note>"
#   event: market-closed | failure | halt | resume | manual | note
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/log/ops_log.jsonl"

event="${1:?usage: oplog.sh <market-closed|failure|halt|resume|manual|note> \"note\"}"
note="${2:?usage: oplog.sh <event> \"note\"}"

case "$event" in
  market-closed|failure|halt|resume|manual|note) ;;
  *) echo "error: unknown event '$event'" >&2; exit 2 ;;
esac

# Note is a single line; embedded newlines would break the JSONL contract.
note="${note//$'\n'/ }"
ts="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
day="$(TZ=America/New_York date +%Y-%m-%d)"

mkdir -p "$ROOT/log"
python3 - "$LOG" "$ts" "$day" "$event" "$note" <<'PY'
import json, sys
path, ts, day, event, note = sys.argv[1:6]
with open(path, "a") as f:
    f.write(json.dumps({"at_utc": ts, "et_date": day,
                        "event": event, "note": note}) + "\n")
PY
echo "logged: $event ($day)"
