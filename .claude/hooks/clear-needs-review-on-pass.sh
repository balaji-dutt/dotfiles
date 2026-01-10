#!/usr/bin/env bash
set -euo pipefail

# If run manually (stdin is a TTY), don't block waiting for JSON.
if [[ -t 0 ]]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

cd "$PROJECT_DIR"

SENTINEL=".claude/.needs_dotfiles_review"
[[ -f "$SENTINEL" ]] || exit 0

# Pick a Python
PY="python3"
command -v python3 >/dev/null 2>&1 || PY="python"
command -v "$PY" >/dev/null 2>&1 || exit 0

# Extract transcript_path from the hook JSON (stdin)
TRANSCRIPT_PATH="$("$PY" -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))' || true)"
[[ -n "$TRANSCRIPT_PATH" ]] || exit 0

# Expand ~/... if present
TRANSCRIPT_PATH="${TRANSCRIPT_PATH/#\~\//$HOME/}"

# If Claude gives a Windows path like C:\Users\..., convert for Git Bash if cygpath exists
if [[ "$TRANSCRIPT_PATH" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  TRANSCRIPT_PATH="$(cygpath -u "$TRANSCRIPT_PATH")"
fi

# For debugging in case sentinel check does not get cleared on Windows
# echo "dotfiles-reviewer: transcript_path=$TRANSCRIPT_PATH" >> "$HOME/claude-hook-debug.log" 2>/dev/null || true

# Only scan the tail so an older PASS doesn't clear a new gate
if tail -n 120 "$TRANSCRIPT_PATH" 2>/dev/null | grep -q 'DOTFILES_REVIEWER_RESULT=PASS'; then
  rm -f "$SENTINEL"
fi
