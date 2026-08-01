<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Agent Worktree Merge Helper

`assets/agent-wt-merge` is a repo-local helper for agents landing worktree
branches back onto `main` or `master`. The copy in the checked-out main
worktree is authoritative, even when the agent is running from an older
feature worktree.

It exists so OpenCode and Claude skills do not need to rebuild the same shell or
Python snippets every time they merge a feature worktree.

## Commands

Run from the feature worktree root. Agent skills resolve and quote the helper
path from the checked-out main worktree before invoking it:

```sh
"<main-worktree>/assets/agent-wt-merge" inspect [--fetch] [--json]
"<main-worktree>/assets/agent-wt-merge" ff --actor opencode|claude [--update-main] [--close-beads <issue-id>]
"<main-worktree>/assets/agent-wt-merge" no-ff --actor opencode|claude -m "<subject>" -m "<body>" [--update-main] [--close-beads <issue-id>]
```

- `inspect` reports merge facts and cleanup suggestions.
- `ff` runs only `git merge --ff-only` from the main worktree.
- `no-ff` runs only `git merge --no-ff` from the main worktree and uses the
  selected actor for merge commit authorship.

The working directory remains the feature worktree, so the helper still gets
the source branch and repository from the caller. Invoking a known feature
worktree copy manually delegates to the main copy when one is available. If no
main copy exists, the local copy reports that it is a fallback. Agents must get
approval before using that fallback or the `--use-local-helper` bootstrap/test
override.

Before invoking a main helper, agents check that selected helper path for
uncommitted changes. The helper repeats this check before delegation. When an
approved `--update-main` fast-forwards main, the running process resolves and
executes the newly checked-out main helper before it starts the feature merge.
It removes `--update-main` to prevent an update loop and removes
`--use-local-helper` so the updated main policy becomes authoritative.

Running the helper from `main` or `master` is always an error because there is
no feature worktree to merge. The helper performs no Git or Beads mutation in
that case.

The helper never pushes, removes a worktree, force-deletes a branch, or closes a
Beads issue without an explicit issue ID.

## Inspect output

`inspect --json` is the agent-facing interface. It reports:

- current feature branch and SHA;
- `main`/`master` branch and checked-out worktree path;
- whether local main is dirty;
- whether local main is behind `origin/<main>`;
- whether fast-forward is possible;
- helper path and provenance (`canonical`, `delegated`, `fallback`, or
  `override`), including post-update re-execution;
- optional matching `ai-wt` session metadata;
- OpenCode and Claude Beads state validation;
- cleanup commands to offer after the merge.

`ai-wt` metadata is optional. The helper uses Git worktree facts as the source of
truth and only uses `.ai-wt/sessions/*.json` to improve cleanup suggestions.

## Beads closure

The merge commands accept `--close-beads <issue-id>`. Closure happens only after
the merge succeeds and only when the actor-specific state file validates:

- OpenCode: `.beads/in-progress-opencode.json`
- Claude: `.beads/in-progress-claude.json`

The state file must contain the explicit issue ID, the current feature branch,
the current worktree path, and a usable `started_sha`. `branch` means the actual
Git branch, not the worktree directory basename or a session-suffixed worktree
label. `worktree_path` means the Git worktree root. If any check fails, the
helper reports the mismatch and leaves the state file untouched.

On success, the helper prints the exact close reason sent to `bd close`. The
reason contains commits introduced by the feature merge session, excluding
unrelated main-only or pre-session commits. A requested close that does not
complete, or a successful close followed by state-file cleanup failure, exits
non-zero and states that the Git merge already succeeded. Do not rerun or roll
back the merge in response to that partial result.

## Cleanup

Cleanup is intentionally not automated. After a successful merge, the helper
prints suggested commands and the workdir to run them from.

For an `ai-wt` worktree, the suggestion is usually:

```sh
ai-wt cleanup <session-id> --delete
```

For other worktrees, the suggestion is usually:

```sh
git worktree remove <worktree-path>
git branch -d <feature-branch>
```

Agents must ask before running cleanup. They must not replace `git branch -d`
with `git branch -D` unless Mr. Dutt explicitly requests a forced delete.

## Permission model

OpenCode permissions for this helper belong in the project config because the
helper is repo-local. The allow rules accept quoted or unquoted absolute paths
anchored under `*/dotfiles/` for the `assets/agent-wt-merge` and
`.opencode/bin/agent-wt-merge` candidates, but only for `inspect`, `ff`, and
`no-ff`. Cleanup commands remain ask-gated so a merge cannot silently remove
worktrees or branches.
