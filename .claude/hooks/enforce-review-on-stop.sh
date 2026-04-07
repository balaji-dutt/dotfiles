#!/usr/bin/env bash
set -euo pipefail

# Read hook payload (Claude provides JSON via stdin; OpenCode/OMO may provide empty/different shapes).
PAYLOAD="$(cat || true)"

# Resolve project dir (works for Claude + OpenCode + oh-my-opencode hook runner).
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

classify_path() {
  local p="${1//\\//}"
  p="${p#./}"
  p="${p,,}"

  if [[ "$p" == "assets/readme.md" ]]; then
    printf 'exempt-doc\n'
    return
  fi

  if [[ "$p" == docs/* ]]; then
    if [[ "$p" == docs/agents/* ]]; then
      printf 'reviewed-doc\n'
    else
      printf 'exempt-doc\n'
    fi
    return
  fi

  if [[ "$p" == "readme.md" || "$p" == "agents.md" || "$p" == "dot_claude/agents.md" ]]; then
    printf 'reviewed-doc\n'
    return
  fi

  printf 'normal\n'
}

# Nothing to enforce
if [[ ! -f "$SENTINEL_OPENCODE" && ! -f "$SENTINEL_CLAUDE" ]]; then
  exit 0
fi

# If all pending changes are exempt docs, do not block stopping.
mapfile -t CHANGED_FILES < <(
  {
    git diff --name-only
    git diff --name-only --cached
    git ls-files --others --exclude-standard
  } | tr -d '\r' | sed '/^$/d' | sort -u
)

if [[ "${#CHANGED_FILES[@]}" -gt 0 ]]; then
  ALL_EXEMPT_DOCS=1
  for p in "${CHANGED_FILES[@]}"; do
    cls="$(classify_path "$p")"
    if [[ "$cls" != "exempt-doc" ]]; then
      ALL_EXEMPT_DOCS=0
      break
    fi
  done

  if [[ "$ALL_EXEMPT_DOCS" -eq 1 ]]; then
    exit 0
  fi
fi

# ---- Context detection ----
# oh-my-opencode can export CLAUDE_PROJECT_DIR even under OpenCode, so do NOT branch on that.
# Prefer OpenCode detection from env, otherwise treat as Claude only if stdin payload looks like Claude hook JSON.
IS_OPENCODE_ENV=0
if [[ -n "${OPENCODE_PROJECT_DIR:-}" || -n "${CWD:-}" ]]; then
  IS_OPENCODE_ENV=1
fi

IS_CLAUDE_PAYLOAD=0
if [[ "$PAYLOAD" == *'"transcript_path"'* || "$PAYLOAD" == *'"agent_transcript_path"'* ]]; then
  IS_CLAUDE_PAYLOAD=1
fi

# Claude branch only when it really looks like a Claude hook call and we're not obviously in OpenCode.
if [[ "$IS_CLAUDE_PAYLOAD" -eq 1 && "$IS_OPENCODE_ENV" -eq 0 ]]; then
  cat <<'JSON'
{
  "decision": "block",
  "reason": "Dotfiles review required before stopping.\n\nNext:\n1) Run the dotfiles reviewer subagent.\n2) Ensure the reviewer’s FINAL line is:\n   DOTFILES_REVIEWER_RESULT=PASS\n\nOnce PASS is recorded, the SubagentStop hook will clear the review gate automatically.\n"
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
