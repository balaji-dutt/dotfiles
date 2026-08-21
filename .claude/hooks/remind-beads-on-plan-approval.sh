#!/usr/bin/env bash
set -euo pipefail

# PostToolUse hook (matcher: ExitPlanMode). Fires ONLY after ExitPlanMode
# succeeds — i.e. after a plan is approved (inline or via Plannotator). A
# rejected / keep-planning plan is a permission denial that keeps the session
# in plan mode, so the tool never succeeds and this hook never runs.
#
# Purpose: in a Beads-enabled repo, force the create/attach/skip handoff
# documented in .claude/CLAUDE.md ("Beads plan handoff") before the first edit.
#
# The state file is a signal, not authority. Its mere existence used to exit 0,
# so a file left behind by an interrupted close silently disabled this gate
# forever (dots-7iav). lib/beads_state.py now resolves it to one of three
# verdicts, and only a verified-live issue buys silence.

# Consume the hook payload (Claude sends JSON on stdin); we don't need fields
# from it — firing is already gated to ExitPlanMode success by matcher+lifecycle.
cat >/dev/null || true

# Resolve project dir (Claude sets CLAUDE_PROJECT_DIR; fall back to git/pwd).
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$PROJECT_DIR" ]]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi
if [[ "$PROJECT_DIR" =~ ^[A-Za-z]:\\ ]] && command -v cygpath >/dev/null 2>&1; then
  PROJECT_DIR="$(cygpath -u "$PROJECT_DIR")"
fi
cd "$PROJECT_DIR"

# Only act in Beads-enabled repos.
[[ -f ".beads/metadata.json" ]] || exit 0

STATE_FILE=".beads/in-progress-claude.json"
HELPER=".claude/hooks/lib/beads_state.py"
RESOLVER=".claude/hooks/lib/resolve-python.sh"

PY_CMD=()
if [[ -f "$RESOLVER" ]]; then
  # shellcheck source=lib/resolve-python.sh disable=SC1091
  . "$RESOLVER"
  resolve_python || PY_CMD=()
fi

# absent     — no state file; the original create/attach/skip prompt applies.
# stale      — bd answered and the answer disqualifies the file.
# unverified — bd could not answer. Report that as a tooling failure, not as
#              staleness, and never as silence.
VERDICT="absent"
REASON=""

if [[ -f "$STATE_FILE" ]]; then
  VERDICT="unverified"
  if [[ ! -f "$HELPER" ]]; then
    REASON="the Beads gate helper is missing ($HELPER)"
  elif [[ ${#PY_CMD[@]} -eq 0 ]]; then
    REASON="no working Python 3 to run the Beads gate helper"
  else
    # The exact exit code matters (0/10/20), not just success, so errexit is
    # lifted around the call rather than using it as an `if` condition.
    set +e
    REASON="$("${PY_CMD[@]}" "$HELPER" check --state-file "$STATE_FILE")"
    helper_rc=$?
    set -e
    case "$helper_rc" in
      0) exit 0 ;; # verified live — stay quiet, as before
      10) VERDICT="stale" ;;
      *)
        VERDICT="unverified" # 20, or any unexpected code
        # A crashing interpreter (helper syntax error, a `py -3` launcher
        # failure) exits non-zero with nothing on stdout, which would leave
        # the message trailing an empty reason.
        REASON="${REASON:-the Beads gate helper exited ${helper_rc}}"
        ;;
    esac
  fi
fi

HANDOFF="Ask once: (1) create a new Beads issue from this plan [default], (2) attach to an existing issue (collect ID), or (3) skip Beads for this session.

If create/attach: delegate to the beads-issue-author subagent with the EXACT ExitPlanMode plan file path (~/.claude/plans/<slug>.md, or ~/.plannotator/plans/<slug>-YYYY-MM-DD-approved.md under claude-plannotator). For a backlog-only plan, route to beads-backlog-manager instead (see the disambiguation rules).

Reply 'skip' to proceed without a Bead."

case "$VERDICT" in
  stale)
    BODY="[BEADS_GATE: stale] Approved plan in a Beads-enabled repo. ${STATE_FILE} is STALE — ${REASON} — so it does not represent live work and cannot stand in for the Beads plan handoff in .claude/CLAUDE.md.

Before the first edit, confirm with the user whether to delete the stale ${STATE_FILE}, then complete the handoff.

${HANDOFF}"
    ;;
  unverified)
    BODY="[BEADS_GATE: unverified] Approved plan in a Beads-enabled repo. The Beads plan gate COULD NOT VERIFY ${STATE_FILE}: ${REASON}.

This is a Beads tooling failure, not a statement about the issue — its status is unknown, and it may well still be live. Surface this to the user and fix the tooling (start the Dolt server in WSL2, or put bd on PATH) rather than working around it. See AGENTS.md, 'Beads conventions'.

If the user chooses to proceed regardless, complete the Beads plan handoff in .claude/CLAUDE.md.

${HANDOFF}"
    # Loud on stderr as well, so the failure lands in the transcript rather
    # than only in the model's context.
    printf 'remind-beads-on-plan-approval: %s\n' "$REASON" >&2
    ;;
  *)
    BODY="[BEADS_GATE: absent] Approved plan in a Beads-enabled repo. Before the first edit, complete the Beads plan handoff in .claude/CLAUDE.md.

${HANDOFF}"
    ;;
esac

# Emit the block payload. BODY always carries quotes and newlines, and on the
# Python path REASON is helper output that can add backslashes too, so the
# reason is encoded rather than interpolated raw.
if [[ ${#PY_CMD[@]} -gt 0 ]]; then
  BEADS_GATE_BODY="$BODY" "${PY_CMD[@]}" -c 'import json, os, sys
sys.stdout.write(json.dumps(
    {"decision": "block", "reason": os.environ["BEADS_GATE_BODY"]},
    indent=2,
) + "\n")'
else
  # Hand-rolled JSON string escape: backslash first, then quote, then fold
  # real newlines to \n. Order matters — escaping backslashes last would
  # double-escape the ones introduced by the earlier steps. Only the three
  # fixed no-Python bodies reach here (absent, helper-missing, no-Python),
  # all backslash-free, so the remaining C0 escapes (\b, \f) cannot occur.
  esc="${BODY//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  esc="${esc//$'\t'/\\t}"
  esc="${esc//$'\r'/}"
  esc="${esc//$'\n'/\\n}"
  printf '{\n  "decision": "block",\n  "reason": "%s"\n}\n' "$esc"
fi

exit 0
