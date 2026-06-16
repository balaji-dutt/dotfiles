#!/usr/bin/env bash
# gate-bd-destructive.sh — Claude Code PreToolUse hook.
#
# Per-subagent guardrail for destructive Beads (`bd`) subcommands. Claude Code
# agent frontmatter only gates at the tool level (`tools: Read, Bash`), so it
# cannot express OpenCode's per-agent "Bash yes, but `bd close` ask / `bd
# delete` deny" posture declaratively. This hook restores that nuance by
# branching on the `agent_type` field the PreToolUse payload carries.
#
# Policy — applied ONLY when the call originates from one of the named beads
# subagents (agent_type is empty for the top-level agent and for skill flows
# such as beads-work, which run at the top level and are intentionally not
# gated here):
#
#   beads-issue-author     deny  bd close | bd delete | bd reopen
#   beads-backlog-manager  deny  bd delete | bd reopen
#                          ask   bd close   (mirrors OpenCode's `bd close*: ask`)
#
# This is defense-in-depth only; the subagents' prompt contracts remain the
# primary guardrail. The hook fails OPEN (exit 0, no decision) on any missing
# tool, unparseable payload, or absent field, so it can never wedge unrelated
# Bash calls — a false negative (gate silently skipped) is preferred over
# blocking legitimate work.

set -uo pipefail

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

# jq is required to parse the command safely. Without it, defer to the normal
# permission system rather than risk blocking every Bash call.
command -v jq >/dev/null 2>&1 || exit 0

agent="$(printf '%s' "$payload" | jq -r '.agent_type // empty' 2>/dev/null || true)"
case "$agent" in
  beads-issue-author | beads-backlog-manager) ;;
  *) exit 0 ;;
esac

cmd="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"
[ -n "$cmd" ] || exit 0

# Detect a destructive `bd` subcommand anywhere on the line, tolerating leading
# `source ...;` chains, `&&`, pipes, and leading whitespace. Deny-eligible
# subcommands (delete/reopen) are checked before the ask-eligible one (close),
# so a command that chains both resolves to the more restrictive outcome.
subcmd=""
if printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])bd[[:space:]]+delete([[:space:]]|$)'; then
  subcmd="delete"
elif printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])bd[[:space:]]+reopen([[:space:]]|$)'; then
  subcmd="reopen"
elif printf '%s' "$cmd" | grep -qE '(^|[;&|[:space:]])bd[[:space:]]+close([[:space:]]|$)'; then
  subcmd="close"
fi
[ -n "$subcmd" ] || exit 0

# Emit a PreToolUse permission decision and exit. $1 = deny|ask, $2 = reason.
decide() {
  local reason_json
  reason_json="$(printf '%s' "$2" | jq -R -s '.')"
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"%s","permissionDecisionReason":%s}}\n' \
    "$1" "$reason_json"
  exit 0
}

case "$agent" in
  beads-issue-author)
    decide deny "beads-issue-author must never run 'bd ${subcmd}'. It only creates or attaches and claims a single issue. Route closes/reopens to the beads-work skill or the top-level agent."
    ;;
  beads-backlog-manager)
    case "$subcmd" in
      delete | reopen)
        decide deny "beads-backlog-manager must never run 'bd ${subcmd}'. Its contract forbids deletion and reopening."
        ;;
      close)
        decide ask "beads-backlog-manager requested 'bd close'. Confirm this close comes from an approved backlog handoff with an explicit issue ID and reason."
        ;;
    esac
    ;;
esac

exit 0
