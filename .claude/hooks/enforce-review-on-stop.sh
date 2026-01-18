#!/usr/bin/env bash
set -euo pipefail

# Read hook JSON payload (Claude provides JSON via stdin; OpenCode/OMO may provide empty or different shapes).
PAYLOAD="$(cat || true)"

# Resolve project dir for both Claude Code + OpenCode (+ oh-my-opencode hook runner).
# Prefer CWD (if provided), then OPENCODE_PROJECT_DIR, then CLAUDE_PROJECT_DIR.
PROJECT_DIR="${CWD:-${OPENCODE_PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-}}}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# If PROJECT_DIR is a Windows path, convert for Git Bash/MSYS
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi

cd "$PROJECT_DIR"

SENTINEL_OPENCODE=".opencode/.needs_dotfiles_review"
SENTINEL_CLAUDE=".claude/.needs_dotfiles_review" # legacy / transitional

# Nothing to enforce
if [[ ! -f "$SENTINEL_OPENCODE" && ! -f "$SENTINEL_CLAUDE" ]]; then
  exit 0
fi

# Detect "real Claude Code hook payload" by presence of known Claude hook fields.
# (Claude hooks provide JSON via stdin and include transcript paths for Stop/SubagentStop.)
IS_CLAUDE_PAYLOAD=0
if [[ "$PAYLOAD" == *'"transcript_path"'* || "$PAYLOAD" == *'"agent_transcript_path"'* ]]; then
  IS_CLAUDE_PAYLOAD=1
fi

# If it's Claude, emit Claude decision JSON. Otherwise, emit OpenCode-friendly instructions.
if [[ "$IS_CLAUDE_PAYLOAD" -eq 1 ]]; then
  cat <<'JSON'
{
  "decision": "block",
  "reason": "Dotfiles review required before stopping.\n\nNext:\n1) Run the dotfiles reviewer subagent.\n2) Ensure the reviewer’s FINAL line is:\n   DOTFILES_REVIEWER_RESULT=PASS\n"
}
JSON
  exit 0
fi

# OpenCode (or OMO running in OpenCode): block with a message that causes an agent invocation, not file-hunting.
cat <<'JSON'
{
  "decision": "block",
  "reason": "Dotfiles review required before stopping.\n\nDo this next (invoke the agent explicitly):\n\n@dotfiles-reviewer\nReview ONLY the latest git changes (use git diff) and end with exactly one of:\nDOTFILES_REVIEWER_RESULT=PASS\nDOTFILES_REVIEWER_RESULT=FAIL\n\nIf FAIL: fix Must-fix issues and rerun the agent.\n\nNote for Mr. Dutt (human): If PASS was returned but stopping is still blocked, the required OpenCode gate-clearing plugin may not have loaded/run. Verify .opencode/plugins/dotfiles-review-gate.js exists and restart OpenCode. If still blocked, manually clear the gate file.\n"
}
JSON
exit 0
