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
"<main-worktree>/assets/agent-wt-merge" prepare-ci [--poll-interval <seconds>] [--poll-timeout <seconds>]
"<main-worktree>/assets/agent-wt-merge" ff --actor opencode|claude [--update-main] [--close-beads <issue-id>]
"<main-worktree>/assets/agent-wt-merge" no-ff --actor opencode|claude -m "<subject>" -m "<body>" [--update-main] [--close-beads <issue-id>]
```

- `inspect` reports merge facts and cleanup suggestions.
- `prepare-ci` normally publishes the exact current feature SHA, verifies the
  advertised remote feature ref, and polls its required CI job. Defaults are a
  15-second interval and a 15-minute timeout.
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

The helper never pushes main, another branch, or tags. `prepare-ci` publishes
only the captured SHA to the same-named remote feature branch without forcing.
After a merge backed by real CI success, `ff` and `no-ff` delete only that remote
feature ref with an exact lease. The helper never removes a local worktree,
force-deletes a local branch, or closes a Beads issue without an explicit issue
ID.

## Inspect output

`inspect --json` is the agent-facing interface. It reports:

- current feature branch and SHA;
- `main`/`master` branch and checked-out worktree path;
- whether local main is dirty;
- the advertised remote-main SHA and whether local main is ahead or behind it;
- whether fast-forward is possible;
- helper path and provenance (`canonical`, `delegated`, `fallback`, or
  `override`), including post-update re-execution;
- optional matching `ai-wt` session metadata;
- OpenCode and Claude Beads state validation;
- feature-worktree lock state and its optional Git lock reason;
- an explicit cleanup policy with `action`, `manager`, `workdir`, `commands`,
  and a human-readable `note`.

`ai-wt` metadata is optional. The helper uses Git worktree facts as the source of
truth and only uses `.ai-wt/sessions/*.json` to improve cleanup suggestions.

## Feature publication and CI gate

OpenCode and Claude install global, local-only post-commit adapters. They are
inactive unless a non-main repository has an executable merge helper and a
valid `configs/gitlab-pipeline-guard.json`. After a successful wrapper commit
that changes HEAD, they inject agent guidance derived from that repository's
helper and `required_job`. The adapters do not contact GitLab or mutate Git.

Commit permission does not authorize publication. The agent must separately
request permission for `prepare-ci`. That command captures the current feature
SHA and normally pushes only:

```text
<captured-sha>:refs/heads/<same-feature-branch>
```

It rejects non-fast-forward conflicts, verifies the exact advertised SHA with
`ls-remote`, and checks the policy's exact-SHA job. Missing and active jobs are
retryable; terminal failure, malformed responses, policy errors, and API errors
fail immediately. Timeout leaves the published feature available for diagnosis
and does not change local main.

The common-directory `pipeline-guard.override` is an explicit bypass rather
than CI success. Publication is still required, and a bypassed merge retains
the remote feature branch.

Every merge command repeats a required fetch, pins the feature SHA, verifies
the remote feature advertises that SHA, and performs a fresh one-shot CI check
before changing main. A no-ff merge additionally requires local main to equal
the freshly advertised remote main; fast-forward may proceed while local main
is ahead when the feature contains it. The merge targets the pinned SHA rather
than the movable branch name.

After real CI success and local merge, the helper deletes the remote feature
with an exact expected-SHA lease and verifies its absence. A missing ref is
already clean. A moved ref is never deleted. Cleanup failure is a post-merge
partial failure: the merge is not rolled back, and Beads closure is still
attempted and reported independently.

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

## Local cleanup

Cleanup is intentionally not automated. The `cleanup.action` field determines
whether an agent may offer commands:

| Condition | `action` | `manager` | Result |
| --- | --- | --- | --- |
| Lock reason contains `aoe-managed` | `defer` | `aoe` | AoE owns teardown; commands are empty |
| Native Windows with matching `ai-wt` metadata | `defer` | `ai-wt` | The wrapper cleans up after the active agent exits |
| Native Windows without `ai-wt` metadata | `defer` | `user` | Clean up outside the session after the active agent exits |
| Other platform with matching `ai-wt` metadata | `suggest` | `ai-wt` | Offer the wrapper cleanup command |
| Other worktree | `suggest` | `git` | Offer non-forced Git cleanup commands |

The AoE marker comparison is case-insensitive. The helper preserves the full
reason in `feature_worktree.lock_reason` for display and diagnostics. Other Git
locks remain visible but do not change policy; agents must not add `--force` to
work around them.

For a suggested `ai-wt` cleanup, the command is usually:

```sh
ai-wt cleanup <session-id> --delete --yes
```

The helper includes `--yes` because agents run commands without a terminal.
Only run the suggestion after the user explicitly approves cleanup; `--yes`
does not imply `--force`.

For a generic worktree, the suggestion is usually:

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
`.opencode/bin/agent-wt-merge` candidates for `inspect`, `ff`, and `no-ff`.
Explicit `prepare-ci` patterns remain ask-gated because commit or inspection
permission does not authorize publication. Invoking `ff` or `no-ff` authorizes
only the helper's lease-protected remote feature deletion after real CI success;
it never authorizes pushing main or tags. Local cleanup commands remain
ask-gated.
