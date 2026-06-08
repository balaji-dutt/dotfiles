# Claude Code — repo-specific guidance

This file is loaded automatically by Claude Code (top-level conversation
and custom subagents) when running in this repo. For workflow rules that
apply to both Claude Code and OpenCode, see `AGENTS.md` at the repo root.

## Beads plan handoff (Claude Code)

After Claude Code's plan mode is approved via `ExitPlanMode`, the top-level
agent — and the `agent-engineer` and `special-builder` subagents — must
offer to capture the plan as a Beads issue before the first edit.

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

### Identity constants

- Actor/assignee: `Claude` (matches `cc-commit`).
- State file: `.beads/in-progress-claude.json`. Isolated from
  `-opencode.json`; the harnesses must not share state.
- Issue prefix: `dots-` for this repo (see `AGENTS.md` "Beads
  conventions" for the broader Beads rules).
