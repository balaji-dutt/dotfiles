#!/usr/bin/env bash
set -euo pipefail

# SubagentStop hook: drop the reviewer's in-flight record, then clear the
# session's review gate when its last verdict is DOTFILES_REVIEWER_RESULT=PASS
# as a final meaningful line. Detection logic lives in lib/review_gate.py.

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
    # Failing to clear leaves the gate raised, which is the conservative
    # direction, so the exit status is deliberately ignored.
    "${PY_CMD[@]}" "$HELPER" clear || true
  fi
fi

exit 0
