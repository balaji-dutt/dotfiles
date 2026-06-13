# Beads Plan Handoff

This repo opts into creating Beads issues from approved Plannotator plans at
implementation handoff time.

This file is for implementation handoff only. For backlog-only, story-only,
ticket-only, planning-only, or existing-Bead enrichment requests, use
`.opencode/instructions/beads-backlog-workflow.md` instead.

## Scope

These instructions apply only to build-capable entrypoints in this repo:

- `build`
- `special-builder`
- `agent-engineer` after planning/design is approved and implementation is
  about to begin

Planning agents (`plan`, `plan-GPT-xhigh`, and any Plan-mode equivalent) must
not create Beads issues, call `beads-issue-author`, or mutate Beads state.
They must also not call `beads-backlog-manager`; backlog-only mutations require
an approved `submit_plan` handoff to a build-capable entrypoint.

## Startup check

Before editing files, check whether the task appears to come from an approved
Plannotator plan.

If the user already supplied a Beads issue ID, do not create a duplicate.
Confirm whether to attach the approved plan to that issue before changing its
design or acceptance fields.

If there is no explicit issue ID, ask once in chat before running any Beads
creation commands:

1. Create a new Beads issue from this approved plan.
2. Use an existing Beads issue ID.
3. Skip Beads for this session.

This is a chat/question gate. Do not run `bd create`, a script, or a subagent
before the user chooses an option.

## State file handling

Treat `.beads/in-progress-opencode.json` as a possible collision signal, not
as authority. Do not silently continue or overwrite it.

If it exists, mention the issue ID and branch/worktree metadata when visible,
then ask whether to use it. If the metadata does not match the current
branch/worktree, treat it as ambiguous and ask or skip.

## Delegation

After the user chooses create or attach for implementation work, delegate to
`beads-issue-author`.
Pass only one explicit plan source:

- the approved plan text already present in the current chat, or
- an exact plan file path that the user confirmed in this chat.

Do not ask the subagent to find the newest or latest file under
`~/.plannotator`. Concurrent Agent-of-Empires sessions can have unrelated
approved plans.

For existing issues, preserve the title and existing metadata. The approved
plan should become the top design content for the current work, followed by a
separator and previous design content preserved for reference. Do not replace
existing acceptance criteria unless the user explicitly approves replacement.

## Closure

Do not close a Beads issue merely because a feature-branch commit exists.
Close only after the work lands on `main`/`master`, either through the
worktree-merge workflow or, for direct-on-main work, after the verified commit
exists on the main branch.
