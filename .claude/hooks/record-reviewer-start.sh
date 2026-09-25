#!/usr/bin/env bash
set -euo pipefail

# SubagentStart hook: record that a reviewer subagent is in flight so the
# Stop hook does not re-block while it runs. Only the configured reviewer is
# recorded; the logic lives in lib/review_gate.py.

# If run manually (stdin is a TTY), don't block waiting for JSON.
if [[ -t 0 ]]; then
  exit 0
fi

# Claude-only: OpenCode has no equivalent Stop block to suppress.
if [[ -z "${CLAUDE_PROJECT_DIR:-}" ]]; then
  exit 0
fi

PROJECT_DIR="$CLAUDE_PROJECT_DIR"

# If CLAUDE_PROJECT_DIR is a Windows path, convert for Git Bash/MSYS
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi

cd "$PROJECT_DIR"

HELPER=".claude/hooks/lib/review_gate.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

if [[ -f "$HELPER" && -f "$RESOLVER" ]]; then
  # shellcheck source=lib/resolve-python.sh disable=SC1091
  . "$RESOLVER"
  if resolve_python; then
    # Failing to record leaves Stop blocking as usual, which is the
    # conservative direction, so the exit status is deliberately ignored.
    "${PY_CMD[@]}" "$HELPER" start || true
  fi
fi

exit 0
