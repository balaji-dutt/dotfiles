#!/usr/bin/env bash
set -euo pipefail

# PostToolUse (Write|Edit): raise the session-scoped review gate for
# reviewable in-repo edits. Logic lives in lib/review_gate.py, which reads
# the hook payload from stdin and filters by path (inside this checkout,
# not exempt per .opencode/opencode-tooling.config.jsonc).

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# If PROJECT_DIR is a Windows path, convert for Git Bash/MSYS
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi

cd "$PROJECT_DIR"

HELPER=".claude/hooks/lib/review_gate.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

if [[ -f "$HELPER" && -f "$RESOLVER" ]]; then
  # shellcheck source=lib/resolve-python.sh disable=SC1091
  . "$RESOLVER"
  if resolve_python && "${PY_CMD[@]}" "$HELPER" mark; then
    exit 0
  fi
fi

# Fallback without a working Python: conservative unconditional mark (legacy
# format); never misses a review at the cost of false positives.
mkdir -p .claude
date -u +%s > .claude/.needs_dotfiles_review
