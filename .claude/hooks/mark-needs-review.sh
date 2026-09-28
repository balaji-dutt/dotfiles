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

if ! cd "$PROJECT_DIR"; then
  printf '%s\n' "mark-needs-review: cannot enter project dir $PROJECT_DIR; this change was not marked for review" >&2
  exit 2
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
  if [[ ${#PY_CMD[@]} -gt 0 ]] && "${PY_CMD[@]}" "$HELPER" mark; then
    exit 0
  fi
fi

# Fallback without a working Python: conservative unconditional mark (legacy
# format); never misses a review at the cost of false positives.
mkdir -p .claude
date -u +%s > .claude/.needs_dotfiles_review
