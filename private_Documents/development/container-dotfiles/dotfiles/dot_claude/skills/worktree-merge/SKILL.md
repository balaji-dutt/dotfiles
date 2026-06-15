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
agent rediscover the same Git/worktree/Beads facts. In this dotfiles repo,
prefer the repo-local helper `./assets/agent-wt-merge`.

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
  Beads state for that exact issue.
- If the helper reports dirty `main`/`master`, detached HEAD, missing main
  worktree, no commits to merge, or mismatched Beads state, stop and report the
  reason instead of guessing.

## Primary workflow: helper available

### 1. Inspect the merge state

From the feature worktree root, run:

```bash
./assets/agent-wt-merge inspect --fetch --json
```

Use the returned JSON as the source of truth:

- `feature_branch`, `main_branch`, and `main_worktree` identify what will be
  merged and where.
- `main_dirty` / `main_dirty_paths` must be clean before merge.
- `origin.behind_count > 0` means you must ask before using `--update-main`.
- `feature.commits_ahead == 0` means there is nothing to merge.
- `feature.fast_forward_possible` chooses `ff` vs `no-ff`.
- `beads.claude.matches == true` identifies the only issue ID safe to pass to
  `--close-beads`.
- `cleanup.workdir` and `cleanup.commands` are suggestions only; do not run
  them until after the merge and explicit user approval.

If `fetch.ok` is false, surface the warning. Do not fail solely because the
network fetch failed unless the merge requires `--update-main`.

### 2. Decide optional flags

- Add `--update-main` only after asking the user when inspect shows local
  `main`/`master` is behind `origin/<main>`.
- Add `--close-beads <issue-id>` only when `beads.claude.matches` is true. If
  the state is absent or mismatched, omit the flag and report why the issue was
  not closed.

### 3. Fast-forward when possible

If `feature.fast_forward_possible` is true, run:

```bash
./assets/agent-wt-merge ff --actor claude [--update-main] [--close-beads <issue-id>]
```

Use only the optional flags justified in step 2.

### 4. Use no-ff only when fast-forward is not possible

If fast-forward is not possible, gather message context with simple Git
commands, then draft a descriptive merge commit message:

```bash
git log "<main-branch>..<feature-branch>" --oneline
git diff --stat "<main-branch>...<feature-branch>"
```

Use the repo's documented commit format (`AGENTS.md`/`CLAUDE.md`). The subject
should describe what the branch did, not say only "Merge branch X".

Then run:

```bash
./assets/agent-wt-merge no-ff --actor claude -m "<subject>" -m "<body>" [--update-main] [--close-beads <issue-id>]
```

The helper sets Claude authorship on the merge commit.

### 5. Report and offer cleanup

After a successful helper run, report:

- merge type;
- main SHA before/after;
- whether a Beads issue was closed;
- that nothing was pushed;
- cleanup suggestions from the helper.

Ask before cleanup. If approved, run the suggested cleanup commands from the
reported `cleanup.workdir`. Keep cleanup permission-gated; do not broaden
permissions to make cleanup silent.

## Fallback: helper absent

If `./assets/agent-wt-merge` is absent in another repository, do not invent a
large heredoc or dynamic parser. Ask whether to proceed manually. If approved,
use the minimal manual workflow:

1. Confirm the current branch is not `main`/`master` and not detached.
2. Resolve `main`/`master` and its checked-out worktree with
   `git worktree list --porcelain`.
3. Verify the main worktree is clean.
4. Fetch best-effort; ask before updating local main from origin.
5. Try `git merge --ff-only <feature-branch>` from the main worktree.
6. If fast-forward fails, draft a descriptive no-ff message and run
   `git merge --no-ff` with Claude author/committer env vars.
7. Close Beads only after merge lands and only after validating an explicit
   matching `.beads/in-progress-claude.json` state file.
8. Offer cleanup, but never run it without confirmation.

If any step would require non-trivial parsing, stop and ask the user to copy or
install the helper instead of recreating it inline.

## Final response template

```markdown
## Merged <feature-branch> into <main-branch>

- Merge type: ff | no-ff
- Main SHA before: <short>
- Main SHA after: <short>
- Commits merged: <n>
- Beads issue closed: <id | no, reason>
- Cleanup offered: <commands, not run>
- Pushed: no
```
