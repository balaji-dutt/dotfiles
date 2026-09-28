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

if ! cd "$PROJECT_DIR"; then
  dir="${PROJECT_DIR//\\/\\\\}"
  dir="${dir//\"/\\\"}"
  dir="${dir//$'\t'/\\t}"
  dir="${dir//$'\r'/}"
  dir="${dir//$'\n'/\\n}"
  printf '{\n  "decision": "block",\n  "reason": "%s"\n}\n' \
    "Dotfiles review gate could not be checked: cannot enter the project directory ${dir}.\n\nRestore access to that directory, or set CLAUDE_ENFORCE_REVIEW=0 to stop without the check.\n"
  exit 0
fi

HELPER=".claude/hooks/lib/review_gate.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

if [[ -f "$HELPER" && -f "$RESOLVER" ]]; then
  # A child shell keeps a resolver that fails to parse, trips set -u, or exits from ending this hook.
  PY_CMD=()
  py_rc=0
  # shellcheck disable=SC2016 # The child shell expands these, not this hook.
  py_out="$("$BASH" -euo pipefail -c '. "$1" >&2 || exit 90
declare -F resolve_python >/dev/null || exit 90
resolve_python >&2 || exit 91
printf "%s\n" "${PY_CMD[@]}"' resolve-python "$RESOLVER" </dev/null)" || py_rc=$?
  if [[ $py_rc -eq 0 && -n "$py_out" ]]; then
    while IFS= read -r part; do PY_CMD+=("$part"); done <<<"$py_out"
  fi
  if [[ ${#PY_CMD[@]} -gt 0 ]]; then
    # Not exec: a helper that starts and then fails must still reach the
    # fallback below rather than erroring open on Stop. cmd_enforce writes
    # its block JSON to stdout and is otherwise silent, so buffering it is
    # safe.
    if out="$("${PY_CMD[@]}" "$HELPER" enforce)"; then
      printf '%s' "$out"
      exit 0
    fi
  fi
fi

# Fallback without a working Python: block while any Claude gate file exists.
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
