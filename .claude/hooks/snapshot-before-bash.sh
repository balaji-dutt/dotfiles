#!/usr/bin/env bash
set -euo pipefail

# PreToolUse (Bash|PowerShell): snapshot the dirty set for mark-needs-review-bash.sh.
# Never blocks and never writes stdout. Logic lives in lib/review_gate.py.

# If run manually (stdin is a TTY), don't block waiting for JSON.
if [[ -t 0 ]]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# If PROJECT_DIR is a Windows path, convert for Git Bash/MSYS
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi

cd "$PROJECT_DIR" || exit 0

HELPER=".claude/hooks/lib/review_gate.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

# Guarded, not errexit: a PreToolUse exit 2 would deny the Bash call.
# shellcheck source=lib/resolve-python.sh disable=SC1091
if [[ -f "$HELPER" && -f "$RESOLVER" ]] && . "$RESOLVER" && resolve_python; then
  # A skipped snapshot leaves this command unchecked; the helper says why on stderr.
  "${PY_CMD[@]}" "$HELPER" snapshot >/dev/null || true
fi

exit 0
