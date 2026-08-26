---
name: worktree-merge
description: Merge the current feature branch worktree into main or recover
  exact-SHA CI evidence for a rewritten local main —
  fast-forward when possible, otherwise create a descriptive no-ff merge
  commit attributed to OpenCode, then offer worktree and branch cleanup.
  Triggered by phrases like "merge this branch into main", "merge the
  worktree back to main", "recover rewritten main CI", or a guarded main push
  reporting missing exact-SHA evidence.
license: MIT
compatibility: opencode
metadata:
  audience: dotfiles-maintainer
  workflow: worktree-merge
---

# worktree-merge

Merge the current feature branch into `main` (or `master`) without making each
agent rediscover the same Git/worktree/Beads facts. Treat the helper checked out
on local main as the authoritative landed policy.

The helper works for direct OpenCode, Plannotator, Agent of Empires, manual
Git worktrees, and `ai-wt` worktrees. Treat `.ai-wt` metadata as optional; it
only improves cleanup suggestions.

## Guardrails

- Never run raw `git push`. Only an explicitly approved `prepare-ci` or
  `prepare-main-ci` invocation may publish the exact captured SHA to its
  narrowly scoped remote ref. The helper may subsequently delete only that ref
  with an exact lease.
- Never push `main`, `master`, another branch, or tags.
- Never run cleanup automatically.
- Never use `git branch -D` in this workflow.
- Never pass `--update-main` unless the user approved updating local
  `main`/`master` from `origin/<main>`.
- Pass `--close-beads <issue-id>` only when inspect reports a matching
  OpenCode Beads state for that exact issue. Respect the repository's Beads
  policy if its helper reports close evidence instead of closing the issue.
- If the helper reports dirty `main`/`master`, detached HEAD, missing main
  worktree, no commits to merge, or mismatched Beads state, stop and report the
  reason instead of guessing.
- A non-zero result after the Git merge may mean remote feature cleanup or the
  requested Beads follow-up failed. Never rerun or roll back the merge; report
  every partial failure and the main SHA that already landed.

## Branch sanity and helper discovery

Before looking for a helper, read the current branch with Git. If it is
`main`/`master`, use **Rewritten main recovery** below; do not enter the feature
merge workflow. If HEAD is detached, stop and report it.

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

## Rewritten main recovery

Use this path only when the guarded local `main`/`master` tip was rewritten and
the main push guard reports missing exact-SHA evidence. Select the executable
helper from that main worktree, verify its path is clean, and ask permission to
run:

```bash
"<merge-helper>" prepare-main-ci
```

This approval authorizes only the helper's recovery ref. It does not authorize
pushing main or tags. The command requires a clean attached guarded main that
is ahead of, not behind, and not diverged from the freshly fetched policy
remote. It first reuses existing successful exact-SHA evidence when available.
Otherwise it publishes only the captured tip to
`refs/heads/ci/<main>/<full-sha>` without forcing, verifies the advertisement,
and polls every 15 seconds for up to 15 minutes.

After real success, the helper revalidates the local tip and advertised remote
main, then exact-lease-deletes only the temporary CI ref. Failure, timeout,
bypass, branch movement, or cleanup failure retains the ref as evidence. An
explicit bypass is not CI success and does not authorize the guarded main push.
Report the result and obtain separate approval for any later main/tag push.

Do not use feature `inspect`, `prepare-ci`, `ff`, or `no-ff` from main. Do not
fall back to a raw temporary-ref push when the guarded helper is unavailable.

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
- `origin.ahead_count > 0` blocks `no-ff`; local main must exactly equal the
  freshly advertised remote main before a no-ff merge.
- `feature.commits_ahead == 0` means there is nothing to merge.
- `feature.fast_forward_possible` chooses `ff` vs `no-ff`.
- `beads.opencode.matches == true` identifies the only issue ID safe to pass
  to `--close-beads`.
- `cleanup.action` is either `suggest` or `defer`. For `suggest`, treat
  `cleanup.workdir` and `cleanup.commands` as permission-gated suggestions.
  For `defer`, report `cleanup.manager` and `cleanup.note`; do not offer or run
  cleanup commands.

If `fetch.ok` is false, surface the warning. Every merge command performs a new
required fetch and fails closed if it cannot establish current remote state.

### 2. Publish and verify the exact feature tip

Ask permission to run this separately approval-gated command:

```bash
"<merge-helper>" prepare-ci
```

Commit approval does not authorize this network operation. `prepare-ci`
captures the current feature SHA, publishes only that SHA to the same-named
remote feature branch without forcing, verifies the advertised ref, and polls
the policy's exact-SHA job every 15 seconds for up to 15 minutes.

Stop if permission is denied or the command reports a conflict, terminal
failure, malformed/unreachable API, or timeout. Missing or active jobs are the
only retryable states. A timeout leaves the feature published and main
untouched. Every later feature commit changes the gated SHA, so run
`prepare-ci` again.

The common-directory override is an explicit bypass, not CI success. It still
requires exact publication. A bypassed merge retains the remote feature branch
as evidence.

### 3. Decide optional flags

- Add `--update-main` only after asking the user when inspect shows local
  `main`/`master` is behind `origin/<main>`. The merge helper must then start a
  fresh process from the updated main helper before merging; require the final
  report to show `helper.reexecuted_after_main_update` as true.
- Add `--close-beads <issue-id>` only when `beads.opencode.matches` is true.
  If the state is absent or mismatched, omit the flag and report why the issue
  was not closed.

### 4. Fast-forward when possible

If `feature.fast_forward_possible` is true, run:

```bash
"<merge-helper>" ff --actor opencode [--update-main] [--close-beads <issue-id>]
```

Use only the optional flags justified in step 2.

### 5. Use no-ff only when fast-forward is not possible

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
"<merge-helper>" no-ff --actor opencode -m "<subject>" -m "<body>" [--update-main] [--close-beads <issue-id>]
```

The helper sets OpenCode authorship on the merge commit.

Both merge commands fetch again, require the remote feature to advertise the
pinned SHA, rerun the exact-SHA check, and merge the SHA rather than the movable
branch name. After real CI success, the helper exact-lease-deletes only the
remote feature ref before attempting Beads closure. It never pushes main or
tags. If the remote feature moved or deletion failed after the merge, report
the partial failure; do not retry the merge or delete the moved ref.

### 6. Report cleanup policy

After a successful helper run, report:

- helper path and provenance state;
- merge type;
- main SHA before/after;
- whether a Beads issue was closed and its exact close reason, or why closure
  was incomplete;
- whether the exact feature SHA was published and passed or bypassed CI;
- whether the remote feature was deleted, already absent, retained for bypass,
  or could not be cleaned safely;
- that main and tags were not pushed;
- whether cleanup was suggested or deferred, including its manager and note.

When `cleanup.action` is `suggest`, ask before cleanup. If approved, run the
reported commands from `cleanup.workdir`. Keep cleanup permission-gated. When
the action is `defer`, do not ask to run cleanup; the named manager owns it.

## Manual fallback: helper absent

If all four helper paths are absent, do not invent a large heredoc or dynamic
parser. Ask whether to proceed manually. If approved, use the minimal manual
workflow:

If `configs/gitlab-pipeline-guard.json` exists, fail closed instead: manual
fallback cannot reproduce the repository's publication, exact-SHA CI, and
lease-protected cleanup contract. Ask the user to restore an authoritative
helper.

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
   `cd "$MAIN_WT" && git merge --no-ff` with OpenCode author/committer env vars.
7. Do not close Beads manually. Report that the issue remains open and leave
   `.beads/in-progress-opencode.json` in the feature worktree untouched.
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
- Feature CI: <required job and exact SHA: success | explicit bypass>
- Remote feature: <deleted | already absent | retained | partial failure>
- Beads issue closed: <id and exact reason | no, reason>
- Cleanup: <offered: commands, not run | deferred: manager and reason>
- Main/tags pushed: no
```

For rewritten-main recovery, report instead:

```markdown
## Prepared exact CI for <main-branch>

- Main SHA: <full SHA>
- Required job: <job name: success | explicit bypass | failure>
- Temporary CI ref: <not needed | deleted | retained | partial failure>
- Remote main revalidated: yes | no, reason
- Main/tags pushed: no
```
