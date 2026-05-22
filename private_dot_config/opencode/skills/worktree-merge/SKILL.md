---
name: worktree-merge
description: Merge the current feature branch worktree into main —
  fast-forward when possible, otherwise create a descriptive no-ff merge
  commit attributed to the agent, then offer worktree and branch
  cleanup. Triggered by phrases like "merge this branch into main",
  "fast-forward into main", or "merge the worktree back to main".
license: MIT
compatibility: opencode
metadata:
  audience: dotfiles-maintainer
  workflow: worktree-merge
---

# worktree-merge

Merge the feature branch in the current worktree into `main` (or `master`
if that is the repo's primary branch). Try `--ff-only` first. Fall back to
a descriptive `--no-ff` merge commit authored as the agent. Stop on
conflict. Offer cleanup of the worktree and branch after the merge lands.

## Use this skill when

- The user says "merge this branch into main", "fast-forward into main",
  "merge the worktree back to main", or "land this on main".
- The current session is inside an ai-wt-created worktree and the user
  signals the feature is done.

## Do not use this skill when

- The user wants to push or open a PR — this skill never pushes.
- The user is already on `main`/`master` — abort, there is nothing to
  merge.
- The user wants a rebase-and-merge or squash-merge — those are different
  workflows; ask what they prefer instead of guessing.
- The repo has no `main` or `master` branch — surface and ask.

## Author identity (harness-scoped)

The merge commit must be attributed to the running agent. Use the literal
env-var values from the harness's commit wrapper
(`bin/executable_cc-commit`, `bin/executable_oc-commit`):

- Claude Code: `Claude` / `noreply@anthropic.com`.
- OpenCode: `OpenCode` / `noreply@opencode.ai`.

## Subject/body format (repo-scoped)

The commit message **format** is set by the repo, not the harness. Read the
repo's `CLAUDE.md` or `AGENTS.md` for its documented commit workflow before
drafting the merge subject and body. Defaults when nothing is documented:

- Subject ≤50 chars, imperative mood, no trailing period.
- Blank second line.
- Body bulleted `- ` lines, each <80 chars.

Some repos (e.g. `homelab-IaC`) keep the same 50/72 body structure but use
Conventional Commits for the subject (`type(scope): subject`). If the
repo's docs say so, follow that.

See `references/merge-message-templates.md` for one example of each
convention, with both Claude and OpenCode invocations of the same message.

## Workflow

### Step 1: Sanity check

Confirm the session is inside a git repo and on a non-main branch:

```bash
git rev-parse --is-inside-work-tree
git rev-parse --abbrev-ref HEAD
```

Abort with a clear message if:

- The command fails (not a git repo).
- `HEAD` is detached (output is `HEAD`).
- The current branch is already `main` or `master`.

### Step 2: Resolve the main branch name and the main worktree path

Prefer `main` if it exists locally; otherwise use `master`:

```bash
if git show-ref --verify --quiet refs/heads/main; then
  MAIN_BRANCH="main"
elif git show-ref --verify --quiet refs/heads/master; then
  MAIN_BRANCH="master"
else
  echo "ERROR: neither main nor master exists locally"
  exit 1
fi
```

Locate main's checkout by parsing `git worktree list --porcelain` for the
entry whose `branch` value is `refs/heads/${MAIN_BRANCH}`. Store as
`MAIN_WT`. If `main` is not checked out anywhere, fall back to the repo's
primary working directory (the first entry in `git worktree list`).

If more than one worktree has `main` checked out, abort and ask — that is
an unusual configuration and silent guesses are risky.

### Step 3: Save state, then cd into the main worktree

Save the feature branch name and the original worktree path to shell
variables before changing directory, so step 7 can return cleanly:

```bash
FEATURE_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
ORIG_WT="$(pwd)"
cd "$MAIN_WT"
```

Do not use `git -C "$MAIN_WT" <verb> ...`. The harness allowlists in this
repo match against the literal command prefix (`Bash(git merge:*)` for
Claude Code, `git merge*` for OpenCode); `git -C <path> merge ...` starts
with `git -C`, not `git merge`, so it would be punted to the ask tier on
every invocation. A single `cd` followed by plain `git <verb>` matches
the existing allowlist entries.

**Important: cwd persistence is not guaranteed.** Some harness shell
sessions (notably Claude Code) reset cwd to the project root between
Bash invocations. Treat `cd "$MAIN_WT"` as a per-invocation prefix, not
a one-shot setup. Either:

- Chain the related steps in a single Bash invocation that starts with
  `cd "$MAIN_WT" && <commands>`, **or**
- Re-issue `cd "$MAIN_WT"` at the top of every subsequent Bash call
  that needs to operate on the main worktree (preconditions, ff
  attempt, no-ff fallback, log inspection, return).

A bare `cd "$MAIN_WT"` in step 3 alone does **not** carry forward.

### Step 4: Verify preconditions

Inside `$MAIN_WT`:

```bash
git status --porcelain
```

Abort if the output is non-empty. List the dirty paths to the user.

Fetch from origin best-effort. Surface failure but do not abort if offline:

```bash
git fetch || echo "WARN: git fetch failed; proceeding with local state"
```

If `main` is behind `origin/${MAIN_BRANCH}`, ask the user whether to
fast-forward main from origin first before merging the feature branch in.

### Step 5: Attempt fast-forward

```bash
git merge --ff-only "$FEATURE_BRANCH"
```

On success, record the merged SHA range with:

```bash
git log "@{1}..HEAD" --oneline
```

Then jump to step 7.

### Step 6: Fall back to a no-ff merge commit (if ff fails)

Build a descriptive message body from the branch's commit history and diff
stat:

```bash
git log "${MAIN_BRANCH}..${FEATURE_BRANCH}" --oneline
git diff --stat "${MAIN_BRANCH}...${FEATURE_BRANCH}"
```

Draft a subject that describes **what the branch did**, not "Merge branch
X" boilerplate. Use the repo's commit format (see the "Subject/body format"
section above). See `references/merge-message-templates.md` for one worked
example per convention, each shown for both Claude and OpenCode.

Then run the merge with the agent's authorship env vars. Use the literal
form for the running harness.

Claude Code:

```bash
GIT_AUTHOR_NAME="Claude" GIT_AUTHOR_EMAIL="noreply@anthropic.com" \
GIT_COMMITTER_NAME="Claude" GIT_COMMITTER_EMAIL="noreply@anthropic.com" \
  git merge --no-ff -m "<subject>" -m "<body>" "$FEATURE_BRANCH"
```

OpenCode:

```bash
GIT_AUTHOR_NAME="OpenCode" GIT_AUTHOR_EMAIL="noreply@opencode.ai" \
GIT_COMMITTER_NAME="OpenCode" GIT_COMMITTER_EMAIL="noreply@opencode.ai" \
  git merge --no-ff -m "<subject>" -m "<body>" "$FEATURE_BRANCH"
```

Verify attribution after the merge:

```bash
git log -1 --format='%an <%ae>'
```

Expect `Claude <noreply@anthropic.com>` or `OpenCode <noreply@opencode.ai>`
matching the harness.

### Step 7: Report and return to the original worktree

Print whether the merge was `ff` or `no-ff`, the resulting `main` SHA, and
`git log -1 --stat` of the merge commit.

Return to the feature branch worktree so the rest of the session resumes
in the right place:

```bash
cd "$ORIG_WT"
```

### Step 8: Offer cleanup (never silent, never automatic)

Always offer — never perform without confirmation:

- **Worktree removal.** Detect whether the worktree was created by `ai-wt`
  by checking for `.ai-wt/` state inside `$ORIG_WT`:

  ```bash
  if [ -d "$ORIG_WT/.ai-wt" ]; then
    echo "Suggest: ai-wt cleanup"
  else
    echo "Suggest: git worktree remove \"$ORIG_WT\""
  fi
  ```

  Surface the suggested command and wait for the user's go-ahead.

- **Branch deletion.** Propose only the safe form:

  ```bash
  git branch -d "$FEATURE_BRANCH"
  ```

  Never propose `-D`. If `-d` refuses because the branch is not fully
  merged, that is a real signal that something is off — surface it to
  the user rather than forcing the delete.

- **Push.** Do not offer. The user can push manually if they want to.

## Edge cases

### Detached HEAD

If step 1 detects detached HEAD, abort. There is no feature branch name to
merge from.

### Feature branch has no new commits

If `git log "${MAIN_BRANCH}..${FEATURE_BRANCH}" --oneline` is empty, tell
the user there is nothing to merge and stop before step 5.

### Main worktree dirty

If `git status --porcelain` in step 4 is non-empty, abort with the concrete
file list. Do not stash silently.

### Merge conflict on no-ff

If `git merge --no-ff ...` exits with a conflict in step 6, stop. List the
conflicted paths:

```bash
git diff --name-only --diff-filter=U
```

Point the user at two options:

- `git merge --abort` to back out cleanly.
- Manual resolution followed by `git commit` (still using the agent
  authorship env vars).

Do not attempt to auto-resolve.

### Branch not pushed to origin

Local merge is fine. The skill never requires the branch to exist on
origin.

### Multiple worktrees checked out on main

Unusual configuration. Abort in step 2 and ask the user which to target.

## Output / final report

```markdown
## Merged <feature-branch> into <main-branch>

- Merge type: ff | no-ff
- Main SHA before: <short>
- Main SHA after: <short>
- Commits merged: <n>
- Merge commit (no-ff only): <short> — author <Claude|OpenCode>
- Returned to worktree: <orig-worktree-path>
- Cleanup offered: worktree removal (<ai-wt cleanup | git worktree remove>),
  branch -d <feature-branch>
- Pushed: no
```
