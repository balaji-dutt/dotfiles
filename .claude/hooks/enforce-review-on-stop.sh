#!/usr/bin/env bash
set -euo pipefail

# Stop hook: block stopping while this session's review gate is raised.
# Gates are Claude-only and session-scoped (.claude/.needs_dotfiles_review.*);
# OpenCode enforcement is handled by .opencode/plugins/review-loop-enforcer.js.

# Escape hatch, mirrors OPENCODE_ENFORCE_REVIEW.
case "${CLAUDE_ENFORCE_REVIEW:-1}" in
  0 | false | off) exit 0 ;;
esac

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

PY="python3"
command -v "$PY" >/dev/null 2>&1 || PY="python"

if [[ -f "$HELPER" ]] && command -v "$PY" >/dev/null 2>&1; then
  exec "$PY" "$HELPER" enforce
fi

# Fallback without Python: block while any Claude gate file exists.
for gate in .claude/.needs_dotfiles_review*; do
  if [[ -e "$gate" ]]; then
    cat <<'JSON'
{
  "decision": "block",
  "reason": "Dotfiles review required before stopping.\n\nRun the dotfiles-reviewer subagent; its FINAL line must be exactly:\nDOTFILES_REVIEWER_RESULT=PASS\n\nOnce PASS is recorded, the SubagentStop hook clears the review gate automatically.\n"
}
JSON
    exit 0
  fi
done

exit 0
