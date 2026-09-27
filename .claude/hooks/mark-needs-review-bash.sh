#!/usr/bin/env bash
set -euo pipefail

# PostToolUse / PostToolUseFailure (Bash|PowerShell): gate reviewable files the
# command changed since its snapshot. Logic lives in lib/review_gate.py.

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

cd "$PROJECT_DIR"

HELPER=".claude/hooks/lib/review_gate.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

# shellcheck source=lib/resolve-python.sh disable=SC1091
if [[ -f "$HELPER" && -f "$RESOLVER" ]] && . "$RESOLVER"; then
  if ! resolve_python; then
    # Skip only for an unmodified resolver: marking every command would gate read-only ones.
    if git --no-optional-locks diff --quiet HEAD -- "$RESOLVER" 2>/dev/null; then
      printf '%s\n' "mark-needs-review-bash: no working Python 3; Bash edits were not checked for review" >&2
      exit 0
    fi
  elif "${PY_CMD[@]}" "$HELPER" mark-bash; then
    exit 0
  fi
fi

# Tracked helper files that are missing, broken, or failing may be this command's doing.
mkdir -p .claude
date -u +%s > .claude/.needs_dotfiles_review
exit 0
