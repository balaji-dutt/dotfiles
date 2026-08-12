---
name: worktree-merge
description: Merge the current feature branch worktree into main —
  fast-forward when possible, otherwise create a descriptive no-ff merge
  commit attributed to Claude, then offer worktree and branch cleanup.
  Triggered by phrases like "merge this branch into main", "fast-forward
  into main", or "merge the worktree back to main".
license: MIT
compatibility: claude-code
metadata:
  audience: dotfiles-maintainer
  workflow: worktree-merge
---

# worktree-merge

Merge the current feature branch into `main` (or `master`) without making each
agent rediscover the same Git/worktree/Beads facts. Treat the helper checked out
on local main as the authoritative landed policy.

The helper works for direct Claude Code, Plannotator, Agent of Empires, manual
Git worktrees, and `ai-wt` worktrees. Treat `.ai-wt` metadata as optional; it
only improves cleanup suggestions.

## Guardrails

- Never push.
- Never run cleanup automatically.
- Never use `git branch -D` in this workflow.
- Never pass `--update-main` unless the user approved updating local
  `main`/`master` from `origin/<main>`.
- Pass `--close-beads <issue-id>` only when inspect reports a matching Claude
  Beads state for that exact issue. Respect the repository's Beads policy if
  its helper reports close evidence instead of closing the issue.
- If the helper reports dirty `main`/`master`, detached HEAD, missing main
  worktree, no commits to merge, or mismatched Beads state, stop and report the
  reason instead of guessing.
- A non-zero result after the Git merge may mean only the requested Beads
  follow-up failed. Never rerun or roll back the merge; report the partial
  failure and the main SHA that already landed.

## Branch sanity and helper discovery

Before looking for a helper, read the current branch with Git. If it is
`main`/`master`, stop: there is no feature branch to merge, so do not invoke any
helper. If HEAD is detached, stop and report it.

From the feature worktree root, use `git worktree list --porcelain` to resolve
the worktree that has `refs/heads/main` or `refs/heads/master`. Do not infer it
from directory names. Stop if the branch is absent, not checked out, or
ambiguous.

Choose the first executable helper that exists, in this order:

1. `<main-worktree>/assets/agent-wt-merge`.
2. `<main-worktree>/.opencode/bin/agent-wt-merge`.
3. `./assets/agent-wt-merge` from the feature worktree.
4. `./.opencode/bin/agent-wt-merge` from the feature worktree.

Before executing a main-worktree candidate, run `git status --porcelain
--untracked-files=all -- <helper-relative-path>` against that main worktree. If
the selected path is dirty, stop rather than executing uncommitted policy. Pass
the selected helper as one quoted literal path so spaces remain intact.

If only a feature-local helper is available, disclose that main has no eligible
helper and get approval before a mutating command. The helper must report
`helper.state` as `fallback`. A feature-local helper may instead be used to
bootstrap or test a helper repair while main still has a helper only after the
user explicitly approves the exception; add `--use-local-helper` to every
helper command and require `helper.state` to be `override`.

Use the manual fallback only when none of these helpers is available and the
user approves.

In the commands below, replace `<merge-helper>` with the selected helper path.

## Primary workflow: helper available

### 1. Inspect the merge state

From the feature worktree root, run:

```bash
"<merge-helper>" inspect --fetch --json
```

Use the returned JSON as the source of truth:

- `feature_branch`, `main_branch`, and `main_worktree` identify what will be
  merged and where.
- `helper.path`, `helper.state`, and related provenance fields identify the
  policy copy that actually ran. Stop if they do not match the selected normal,
  fallback, or approved override path.
- `main_dirty` / `main_dirty_paths` must be clean before merge.
- `origin.behind_count > 0` means you must ask before using `--update-main`.
- `feature.commits_ahead == 0` means there is nothing to merge.
- `feature.fast_forward_possible` chooses `ff` vs `no-ff`.
- `beads.claude.matches == true` identifies the only issue ID safe to pass to
  `--close-beads`.
- `cleanup.action` is either `suggest` or `defer`. For `suggest`, treat
  `cleanup.workdir` and `cleanup.commands` as permission-gated suggestions.
  For `defer`, report `cleanup.manager` and `cleanup.note`; do not offer or run
  cleanup commands.

If `fetch.ok` is false, surface the warning. Do not fail solely because the
network fetch failed unless the merge requires `--update-main`.

### 2. Decide optional flags

- Add `--update-main` only after asking the user when inspect shows local
  `main`/`master` is behind `origin/<main>`. The merge helper must then start a
  fresh process from the updated main helper before merging; require the final
  report to show `helper.reexecuted_after_main_update` as true.
- Add `--close-beads <issue-id>` only when `beads.claude.matches` is true. If
  the state is absent or mismatched, omit the flag and report why the issue was
  not closed.

### 3. Fast-forward when possible

If `feature.fast_forward_possible` is true, run:

```bash
"<merge-helper>" ff --actor claude [--update-main] [--close-beads <issue-id>]
```

Use only the optional flags justified in step 2.

### 4. Use no-ff only when fast-forward is not possible

If fast-forward is not possible, gather message context with simple Git
commands, then draft a descriptive merge commit message:

```bash
cd "<main-worktree>" && git log "<main-branch>..<feature-branch>" --oneline
cd "<main-worktree>" && git diff --stat "<main-branch>...<feature-branch>"
```

Use the repo's documented commit format (`AGENTS.md`/`CLAUDE.md`). The subject
should describe what the branch did, not say only "Merge branch X".

Then run:

```bash
"<merge-helper>" no-ff --actor claude -m "<subject>" -m "<body>" [--update-main] [--close-beads <issue-id>]
```

The helper sets Claude authorship on the merge commit.

### 5. Report cleanup policy

After a successful helper run, report:

- helper path and provenance state;
- merge type;
- main SHA before/after;
- whether a Beads issue was closed and its exact close reason, or why closure
  was incomplete;
- that nothing was pushed;
- whether cleanup was suggested or deferred, including its manager and note.

When `cleanup.action` is `suggest`, ask before cleanup. If approved, run the
reported commands from `cleanup.workdir`. Keep cleanup permission-gated. When
the action is `defer`, do not ask to run cleanup; the named manager owns it.

## Manual fallback: helper absent

If all four helper paths are absent, do not invent a large heredoc or dynamic
parser. Ask whether to proceed manually. If approved, use the minimal manual
workflow:

Bash tool calls do not preserve `cd` between invocations. For every manual
command that must run in the main worktree, re-issue `cd "$MAIN_WT" && ...`
in the same command.

1. Confirm the current branch is not `main`/`master` and not detached.
2. Resolve `main`/`master` and its checked-out worktree with
   `git worktree list --porcelain`. Also inspect the current feature record's
   `locked` line because porcelain output preserves the lock reason.
3. Verify the main worktree is clean.
4. Fetch best-effort from the main worktree with
   `cd "$MAIN_WT" && git fetch`; ask before updating local main from origin.
5. Try `cd "$MAIN_WT" && git merge --ff-only <feature-branch>`.
6. If fast-forward fails, draft a descriptive no-ff message and run
   `cd "$MAIN_WT" && git merge --no-ff` with Claude author/committer env vars.
7. Do not close Beads manually. Report that the issue remains open and leave
   `.beads/in-progress-claude.json` in the feature worktree untouched.
8. Classify cleanup before offering it:
   - If the feature lock reason contains `aoe-managed` case-insensitively,
     defer cleanup to AoE and do not offer worktree or branch removal.
   - On native Windows, do not remove the active worktree from this running
     agent session. Defer an `ai-wt` worktree to its wrapper after exit. For an
     unmanaged worktree, report that cleanup must happen outside the exited
     session.
   - Otherwise, offer the normal non-forced cleanup commands, but never run
     them without confirmation.

If any step would require non-trivial parsing, stop and ask the user to copy or
install the helper instead of recreating it inline.

## Final response template

```markdown
## Merged <feature-branch> into <main-branch>

- Merge type: ff | no-ff
- Helper: <path> (<canonical | delegated | fallback | override>)
- Main SHA before: <short>
- Main SHA after: <short>
- Commits merged: <n>
- Beads issue closed: <id and exact reason | no, reason>
- Cleanup: <offered: commands, not run | deferred: manager and reason>
- Pushed: no
```
