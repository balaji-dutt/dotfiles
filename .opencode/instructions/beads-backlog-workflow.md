# Beads Backlog Workflow

This repo supports a dotfiles-only Beads backlog workflow for planning sessions
that should mutate Beads but must not start implementation.

## Scope

Use this workflow for backlog-only requests, including:

- "backlog grooming"
- "create a Bead", "create a task", "create a story", or "create an epic"
- "ticket only", "story only", "planning only", or "no implementation"
- "turn this idea/transcript/plan into Beads"
- "enrich", "update", "refine", or "plan" a named Bead
- "create follow-up", "split out", or "child Bead"
- "prioritize", "re-prioritize", or "link dependencies"
- "close", "release", or "update" a named Bead when no source edits are
  requested

Do not use this workflow when the user asks to implement, fix, work on, patch,
or make the source change. Those requests use the normal implementation flow
and `.opencode/instructions/beads-plan-handoff.md`.

## Classification rules

- If a Bead ID is present, default to updating that existing Bead.
- If the user says "existing" but provides no ID, inspect/list candidates
  read-only and ask which Bead to update before planning a mutation.
- If the user says "follow-up", "child", or "split out", create a linked Bead
  instead of overwriting the original.
- If the request is ambiguous between backlog-only and implementation, ask the
  user which path they want.
- Treat every Beads mutation as Medium/High for planning purposes, even when it
  would not edit source files. It must go through `submit_plan`.

## Planning-agent responsibilities

Planning agents (`plan`, `plan-GPT-xhigh`, and Plan-mode equivalents) may use
read-only Beads inspection such as `bd show` and `bd list` for context. They
must not create, update, close, delete, claim, assign, or otherwise mutate
Beads, and they must not call mutating Beads subagents.

For backlog-only Beads mutations, the plan must include a machine-readable
handoff block:

```md
## Beads backlog handoff

Action: update-existing
Issue: dots-123
No implementation: true

Field updates:
- Replace acceptance criteria with the approved list
- Append design notes from this plan

Preserve:
- Existing title
- Existing history/design notes
- Existing status and assignee
```

Accepted `Action` values are:

- `create`
- `update-existing`
- `create-linked`
- `link`
- `prioritize`
- `update-status`
- `close`

If a proposed plan would mutate Beads but does not clearly include
`No implementation: true`, the build-capable agent must ask for clarification
instead of guessing.

## Handoff after approval

After `submit_plan` approval, build-capable agents (`build`, `special-builder`,
and `agent-engineer`) must route backlog-only plans to `beads-backlog-manager`,
return its result, and stop without source edits.

Pass only one explicit plan source to the subagent:

- approved plan text already present in the current chat, or
- an exact plan file path that the user confirmed in the current chat.

Do not ask the subagent to find the newest or latest file under
`~/.plannotator`.

## Preservation defaults

For existing-Bead enrichment:

- preserve the title unless the approved handoff explicitly requests a retitle;
- preserve status, assignee, labels, priority, parent, dependencies, and
  external references unless the handoff explicitly requests a change;
- preserve original description/context by default;
- replace acceptance criteria only when the handoff explicitly says to replace
  them;
- append new planning or design content under a dated section unless the
  handoff explicitly chooses a different preservation mode;
- do not claim the Bead;
- do not write `.beads/in-progress-opencode.json`.

For newly created backlog Beads:

- pass `--actor "OpenCode"` so the audit trail does not fall through to
  `git user.name`;
- set no assignee. Backlog items stay unassigned until work starts; assignment
  belongs to the claim step in `beads-issue-author` or the `beads-work` skill.
  Pass `--assignee` only when the approved handoff explicitly names one.

For close/status changes, require an explicit `Action`, issue ID, and approved
reason. Deletion is never part of this workflow.
