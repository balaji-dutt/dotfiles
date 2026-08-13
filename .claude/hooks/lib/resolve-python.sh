# shellcheck shell=bash
#
# Resolve a working Python 3 interpreter for the review-gate hooks.
#
# Sourced, not executed. On success sets PY_CMD (array, so a candidate can
# carry arguments such as `py -3`) and returns 0; returns 1 when no candidate
# actually runs.
#
# command -v is not enough on native Windows: the Microsoft Store
# app-execution alias for python3 lives in PATH and is a real executable, but
# running it prints "Python was not found; run without arguments to install
# from the Microsoft Store" and exits 49. Every candidate is therefore
# executed before it is accepted.
#
# The 3.7 floor is what lib/review_gate.py needs (datetime.fromisoformat); it
# also rejects a `python` that is still Python 2.
#
# CLAUDE_REVIEW_GATE_PYTHON overrides the candidate order for debugging. It is
# probed like any other candidate rather than trusted.

PY_CMD=()

resolve_python() {
  local probe='import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)'
  local cand
  local -a parts

  for cand in "${CLAUDE_REVIEW_GATE_PYTHON:-}" python3 python "py -3"; do
    [[ -n "$cand" ]] || continue
    read -r -a parts <<<"$cand"
    command -v "${parts[0]}" >/dev/null 2>&1 || continue
    "${parts[@]}" -c "$probe" >/dev/null 2>&1 || continue
    # shellcheck disable=SC2034  # PY_CMD is this function's output, read by the sourcing hook.
    PY_CMD=("${parts[@]}")
    return 0
  done

  return 1
}
