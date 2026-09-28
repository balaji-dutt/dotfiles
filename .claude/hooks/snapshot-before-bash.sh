#!/usr/bin/env bash
set -euo pipefail

# PreToolUse (Bash|PowerShell): snapshot the dirty set for mark-needs-review-bash.sh.
# Never blocks and never writes stdout. Logic lives in lib/review_gate.py.

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

cd "$PROJECT_DIR" || exit 0

HELPER=".claude/hooks/lib/review_gate.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

# Guarded, not errexit: a PreToolUse exit 2 would deny the Bash call.
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
    # A skipped snapshot leaves this command unchecked; the helper says why on stderr.
    "${PY_CMD[@]}" "$HELPER" snapshot >/dev/null || true
  fi
fi

exit 0
