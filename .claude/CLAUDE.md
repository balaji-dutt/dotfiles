# Claude Code — repo-specific guidance

This file is loaded automatically by Claude Code (top-level conversation
and custom subagents) when running in this repo. For workflow rules that
apply to both Claude Code and OpenCode, see `AGENTS.md` at the repo root.

## Beads plan handoff (Claude Code)

This section covers **implementation handoff only** — the path where an
approved plan will trigger actual source edits. For backlog-only,
story-only, ticket-only, planning-only, or existing-Bead enrichment
requests (no source edits), use **Beads backlog handoff (Claude Code)**
below instead.

After Claude Code's plan mode is approved via `ExitPlanMode`, the top-level
agent — and the `agent-engineer` and `special-builder` subagents — must
offer to capture the plan as a Beads issue before the first edit.

This offer is enforced by the `PostToolUse` / `ExitPlanMode` hook
`.claude/hooks/remind-beads-on-plan-approval.sh`, which fires on plan
approval and blocks with the create/attach/skip prompt.

The hook is silent only when `.beads/in-progress-claude.json` names an issue
that `bd` confirms is still live — `open`, `in_progress`, or `blocked`. The
common case is `in_progress`, since claiming the issue is what writes the
state file. Existence alone is not enough: a state file
whose issue is closed, missing, or unreadable is stale and still prompts, and
a state file that cannot be checked at all (no `bd`, unreachable Dolt server)
prompts with a distinct tooling-failure message rather than a claim about the
issue. The three block messages are tagged `[BEADS_GATE: absent]`,
`[BEADS_GATE: stale]`, and `[BEADS_GATE: unverified]`. Validation lives in
`.claude/hooks/lib/beads_state.py`.

### When this applies

- The current repo is Beads-enabled (`.beads/metadata.json` exists).
- The user just accepted a plan via `ExitPlanMode`. The plan file path
  depends on how the session was launched:
  - Plain `claude` (no Plannotator wrapper): plan file is at
    `~/.claude/plans/<slug>.md`.
  - `claude-plannotator` wrapper (Plannotator intercepts `ExitPlanMode`):
    plan file is exclusively at
    `~/.plannotator/plans/<slug>-YYYY-MM-DD-approved.md`. The approved
    plan text returns inline to the agent, but the only on-disk artifact
    lives under `~/.plannotator/` — `~/.claude/plans/` will NOT contain
    a matching file in this case.
- The lifecycle entry point is **plan-mode → implementation**. The
  `beads-work` skill handles the "work on dots-foo" path; this handoff
  handles the "free-form chat → plan → implement" path.

If the user has not yet exited plan mode, do nothing — wait. Do not
pre-create issues during planning.

### Disambiguation: implementation vs backlog

If the approved plan would mutate Beads but the handoff block does not
contain `No implementation: true`, do not guess which path to take. Ask
the user once whether this is an implementation plan (route here, to
`beads-issue-author`) or a backlog-only plan (route to
`beads-backlog-manager` per the section below).

### Protocol

1. After `ExitPlanMode` approval, before the first edit, ask once in chat:
   1. Create a new Beads issue from this approved plan (default).
   2. Attach the approved plan to an existing Beads issue (collect ID).
   3. Skip Beads for this session.
2. If `.beads/in-progress-claude.json` already exists, mention the issue
   ID and branch/worktree from that file and ask whether to reuse it
   instead of creating a new one. Do not overwrite silently.
3. If the user picks create or attach, delegate to `beads-issue-author`
   via the Agent tool with `subagent_type: beads-issue-author`. Pass:
   - the plan file path (exact, from the ExitPlanMode flow). Use the
     `~/.plannotator/plans/<slug>-YYYY-MM-DD-approved.md` path when the
     session was launched via `claude-plannotator`; otherwise use the
     native `~/.claude/plans/<slug>.md` path. If the path is unknown,
     ask the user once rather than scanning either directory.
   - mode (`create` or `attach`) and `<prefix>-<id>` if attaching;
   - absolute repo path, branch, worktree path, and `started_sha` from
     `git rev-parse HEAD`.
4. Block on the subagent's reply. If it returns `Blocked`, surface the
   reason and ask the user before proceeding to edits.
5. Do not close the resulting issue from a feature-branch commit. Close
   only after the work lands on `main`/`master` (worktree-merge or
   direct), via the `beads-work` skill's close steps.

### Identity constants (implementation handoff)

These govern the implementation path only. Backlog-only work uses the
actor but sets no assignee — see **Preservation defaults** below.

- Actor: `Claude` (matches `cc-commit`). Set on every Beads write.
- Assignee: `Claude`. Set here because this path claims the issue to
  start work. Backlog-only creates leave it empty.
- State file: `.beads/in-progress-claude.json`. Isolated from
  `-opencode.json`; the harnesses must not share state.
- Issue prefix: `dots-` for this repo (see `AGENTS.md` "Beads
  conventions" for the broader Beads rules).

## Beads backlog handoff (Claude Code)

This section covers **backlog-only** work — mutating Beads (create,
enrich, link, prioritize, change status, or close issues) **without**
making any source edits. For plans that will trigger implementation, use
**Beads plan handoff (Claude Code)** above instead.

### When this applies

Use this flow for backlog-only requests, including:

- "backlog grooming"
- "create a Bead / task / story / epic"
- "ticket only", "story only", "planning only", or "no implementation"
- "turn this idea / transcript / plan into Beads"
- "enrich", "update", "refine", or "plan" a named Bead
- "create follow-up", "split out", or "child Bead"
- "prioritize", "re-prioritize", or "link dependencies"
- "close", "release", or "update" a named Bead when no source edits are
  requested

Do not use this flow when the user asks to implement, fix, work on, patch,
or make the source change. Those go through **Beads plan handoff (Claude
Code)** above. The `beads-work` skill still owns the "work on dots-foo"
path.

### Classification rules

- If a Bead ID is present, default to updating that existing Bead.
- If the user says "existing" but provides no ID, inspect/list candidates
  read-only and ask which Bead to update before planning a mutation.
- If the user says "follow-up", "child", or "split out", create a linked
  Bead instead of overwriting the original.
- If the request is ambiguous between backlog-only and implementation, ask
  the user which path they want (see "Disambiguation" above).
- Treat every Beads mutation as a plan-worthy change even when it edits no
  source files: route it through plan mode and `ExitPlanMode` approval.

### Planning must not mutate

While in plan mode you may use read-only Beads inspection (`bd show`,
`bd list`) for context. Do not create, update, close, delete, claim, or
otherwise mutate Beads during planning, and do not call
`beads-backlog-manager` before the plan is approved.

For backlog-only mutations, the approved plan must carry a
machine-readable handoff block:

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
- Existing history / design notes
- Existing status and assignee
```

Accepted `Action` values: `create`, `update-existing`, `create-linked`,
`link`, `prioritize`, `update-status`, `close`.

If a proposed plan would mutate Beads but does not clearly include
`No implementation: true`, ask which path the user wants instead of
guessing.

### Handoff after approval

After `ExitPlanMode` approval, route a backlog-only plan to the
`beads-backlog-manager` subagent via the Agent tool with
`subagent_type: beads-backlog-manager`, return its result, and stop
without any source edits. Pass only one explicit plan source:

- the approved plan text already present in the current chat, or
- an exact plan file path the user confirmed in this chat (native
  `~/.claude/plans/<slug>.md` or Plannotator
  `~/.plannotator/plans/<slug>-YYYY-MM-DD-approved.md`).

Do not ask the subagent to find the newest or latest file under
`~/.claude/plans` or `~/.plannotator`.

### Preservation defaults

For existing-Bead enrichment:

- preserve the title unless the approved handoff explicitly requests a
  retitle;
- preserve status, assignee, labels, priority, parent, dependencies, and
  external references unless the handoff explicitly requests a change;
- preserve the original description / context by default;
- replace acceptance criteria only when the handoff explicitly says to;
- append new planning or design content under a dated section unless the
  handoff explicitly chooses a different mode;
- do not claim the Bead;
- do not write `.beads/in-progress-claude.json` — backlog-only work
  records no implementation state.

For newly created backlog Beads:

- pass `--actor "Claude"` so the audit trail does not fall through to
  `git user.name`;
- set no assignee. Backlog items stay unassigned until work starts;
  assignment belongs to the claim step in `beads-issue-author` or the
  `beads-work` skill. Pass `--assignee` only when the approved handoff
  explicitly names one.

For close / status changes, require an explicit `Action`, issue ID, and
approved reason. Deletion is never part of this flow.
