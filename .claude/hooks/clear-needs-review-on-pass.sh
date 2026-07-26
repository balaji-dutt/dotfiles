#!/usr/bin/env bash
set -euo pipefail

# SubagentStop hook: clear the session's review gate when the reviewer
# transcript ends with DOTFILES_REVIEWER_RESULT=PASS as its final meaningful
# line. Detection logic lives in lib/review_gate.py.

# If run manually (stdin is a TTY), don't block waiting for JSON.
if [[ -t 0 ]]; then
  exit 0
fi

# Claude-only: OpenCode clears its gates via .opencode/plugins/review-loop-gate.js.
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

PY="python3"
command -v "$PY" >/dev/null 2>&1 || PY="python"

if [[ -f "$HELPER" ]] && command -v "$PY" >/dev/null 2>&1; then
  exec "$PY" "$HELPER" clear
fi

exit 0
