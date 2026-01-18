#!/usr/bin/env bash
set -euo pipefail

# If run manually (stdin is a TTY), don't block waiting for JSON.
if [[ -t 0 ]]; then
  exit 0
fi

# Claude-only: if this isn't Claude Code, don't do anything.
# (OpenCode will clear via plugin instead.)
if [[ -z "${CLAUDE_PROJECT_DIR:-}" ]]; then
  exit 0
fi

PROJECT_DIR="$CLAUDE_PROJECT_DIR"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# If CLAUDE_PROJECT_DIR is a Windows path, convert for Git Bash/MSYS
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi

cd "$PROJECT_DIR"

SENTINEL_OPENCODE=".opencode/.needs_dotfiles_review"
SENTINEL_CLAUDE=".claude/.needs_dotfiles_review"

# Prefer the shared OpenCode sentinel, but tolerate legacy Claude sentinel.
SENTINEL=""
if [[ -f "$SENTINEL_OPENCODE" ]]; then
  SENTINEL="$SENTINEL_OPENCODE"
elif [[ -f "$SENTINEL_CLAUDE" ]]; then
  SENTINEL="$SENTINEL_CLAUDE"
else
  exit 0
fi

# Pick a Python
PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"
command -v "$PY" >/dev/null 2>&1 || exit 0

# Read hook JSON once
PAYLOAD="$(cat || true)"
[[ -n "$PAYLOAD" ]] || exit 0

# Extract paths from payload (tolerant of bad JSON)
MAIN_TRANSCRIPT_PATH="$("$PY" - <<'PY' "$PAYLOAD"
import json,sys
try:
    p=json.loads(sys.argv[1])
    print(p.get("transcript_path","") or "")
except Exception:
    print("")
PY
)"

AGENT_TRANSCRIPT_PATH="$("$PY" - <<'PY' "$PAYLOAD"
import json,sys
try:
    p=json.loads(sys.argv[1])
    print(p.get("agent_transcript_path","") or "")
except Exception:
    print("")
PY
)"

# Normalize transcript paths
normalize_path() {
  local p="$1"
  [[ -n "$p" ]] || { echo ""; return; }
  p="${p/#\~\//$HOME/}"
  if [[ "$p" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
    p="$(cygpath -u "$p")"
  fi
  echo "$p"
}

MAIN_TRANSCRIPT_PATH="$(normalize_path "$MAIN_TRANSCRIPT_PATH")"
AGENT_TRANSCRIPT_PATH="$(normalize_path "$AGENT_TRANSCRIPT_PATH")"

# Compute gate epoch from sentinel contents (preferred) else mtime
GATE_EPOCH="$("$PY" - <<'PY' "$SENTINEL"
import os,sys
path=sys.argv[1]

gate=None
try:
    s=open(path,'r',encoding='utf-8',errors='ignore').read().strip()
    if s.isdigit():
        gate=int(s)
except Exception:
    pass

if gate is None:
    try:
        gate=int(os.path.getmtime(path))
    except Exception:
        gate=0

print(gate)
PY
)"

# Helper: scan last N jsonl records for PASS, ignoring meta + user records,
# and require PASS to be newer than the gate time.
has_pass() {
  local path="$1"
  [[ -n "$path" ]] || return 1

  "$PY" - "$path" "$GATE_EPOCH" <<'PY'
import json,sys
from datetime import datetime

path=sys.argv[1]
gate_epoch=float(sys.argv[2])
token="DOTFILES_REVIEWER_RESULT=PASS"
SLACK_SECONDS=3.0

def parse_iso_to_epoch(ts: str):
    if not ts:
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None

try:
    with open(path,'r',encoding='utf-8',errors='ignore') as f:
        lines=f.readlines()[-800:]
except OSError:
    sys.exit(1)

def contains(x):
    if isinstance(x, str):
        return token in x
    if isinstance(x, list):
        return any(contains(i) for i in x)
    if isinstance(x, dict):
        return any(contains(v) for v in x.values())
    return False

for line in lines:
    try:
        obj=json.loads(line)
    except Exception:
        continue

    if obj.get("isMeta") is True:
        continue
    if obj.get("type") == "user":
        continue

    msg=obj.get("message",{})
    if not contains(msg.get("content")):
        continue

    evt_epoch = parse_iso_to_epoch(obj.get("timestamp",""))
    if evt_epoch is None:
        continue

    if evt_epoch + SLACK_SECONDS >= gate_epoch:
        sys.exit(0)

sys.exit(1)
PY
}

# Retry briefly to allow transcript flush; check agent first, then main
for _ in {1..15}; do
  if has_pass "$AGENT_TRANSCRIPT_PATH" || has_pass "$MAIN_TRANSCRIPT_PATH"; then
    rm -f "$SENTINEL_OPENCODE" "$SENTINEL_CLAUDE"
    exit 0
  fi
  sleep 0.2
done

exit 0
