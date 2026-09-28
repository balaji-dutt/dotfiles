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

if ! cd "$PROJECT_DIR"; then
  printf '%s\n' "mark-needs-review-bash: cannot enter project dir $PROJECT_DIR; this change was not marked for review" >&2
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
  if [[ $py_rc -eq 91 ]]; then
    # Skip only for an unmodified resolver: marking every command would gate read-only ones.
    if git --no-optional-locks diff --quiet HEAD -- "$RESOLVER" 2>/dev/null; then
      printf '%s\n' "mark-needs-review-bash: no working Python 3; Bash edits were not checked for review" >&2
      exit 0
    fi
  elif [[ ${#PY_CMD[@]} -gt 0 ]] && "${PY_CMD[@]}" "$HELPER" mark-bash; then
    exit 0
  fi
fi

# Tracked helper files that are missing, broken, or failing may be this command's doing.
mkdir -p .claude
date -u +%s > .claude/.needs_dotfiles_review
exit 0
