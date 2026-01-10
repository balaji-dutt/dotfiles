#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

cd "$PROJECT_DIR"

if [[ ! -f .claude/.needs_dotfiles_review ]]; then
  exit 0
fi

cat <<'JSON'
{
  "decision": "block",
  "reason": "Dotfiles review required before stopping.\n\n1) Run @agent-dotfiles-reviewer on the current diff (use `git diff --no-color`).\n2) Apply fixes/simplifications.\n\nWhen the reviewer returns DOTFILES_REVIEWER_RESULT=PASS, the hook will auto-clear the gate and you can stop."
}
JSON
