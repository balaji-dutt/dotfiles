#!/usr/bin/env bash
set -euo pipefail

# PostToolUse hook (matcher: ExitPlanMode). Fires ONLY after ExitPlanMode
# succeeds — i.e. after a plan is approved (inline or via Plannotator). A
# rejected / keep-planning plan is a permission denial that keeps the session
# in plan mode, so the tool never succeeds and this hook never runs.
#
# Purpose: in a Beads-enabled repo, force the create/attach/skip handoff
# documented in .claude/CLAUDE.md ("Beads plan handoff") before the first edit.

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

# Already captured this session — do not nag.
[[ -f ".beads/in-progress-claude.json" ]] && exit 0

cat <<'JSON'
{
  "decision": "block",
  "reason": "Approved plan in a Beads-enabled repo. Before the first edit, complete the Beads plan handoff in .claude/CLAUDE.md.\n\nAsk once: (1) create a new Beads issue from this plan [default], (2) attach to an existing issue (collect ID), or (3) skip Beads for this session.\n\nIf create/attach: delegate to the beads-issue-author subagent with the EXACT ExitPlanMode plan file path (~/.claude/plans/<slug>.md, or ~/.plannotator/plans/<slug>-YYYY-MM-DD-approved.md under claude-plannotator). For a backlog-only plan, route to beads-backlog-manager instead (see the disambiguation rules).\n\nReply 'skip' to proceed without a Bead."
}
JSON
exit 0
