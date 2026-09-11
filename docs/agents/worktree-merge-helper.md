<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Agent Worktree Merge Helper

`assets/agent-wt-merge` is a repo-local helper for landing worktree branches
back onto `main` or `master`. Humans and agents can invoke it. The copy in the
checked-out main worktree is authoritative, even when the caller is running
from an older feature worktree.

It exists so OpenCode and Claude skills do not need to rebuild the same shell or
Python snippets every time they merge a feature worktree.

CI-gated repositories select GitLab job evidence or GitHub Actions workflow
evidence through an explicit policy. GitLab shares its runtime with
`assets/guarded-main-sync`; its human `gpls` reconciliation remains GitLab-only.
Both providers reuse the exact-ref publication and lease-cleanup primitives.
`agent-wt-merge prepare-main-ci` prepares evidence for a clean, ahead-only
rewritten or batched main tip without pushing main.

## Repository capabilities

Probe the selected helper with `--help`. Its source root, not the caller's
working directory, determines the policy:

- Neither policy: **local-only**, advertising only `inspect`, `ff`, and `no-ff`.
  The standalone helper requires no pipeline runtime, checker, or `gh`.
- `configs/gitlab-pipeline-guard.json`: **CI-gated**, also advertising
  `prepare-ci` and `prepare-main-ci`, with the legacy GitLab job contract.
- `configs/pipeline-guard.json`: **CI-gated**, with GitHub workflow evidence
  and the additional `prune-evidence` command.
- Both policy paths: a conflict, never precedence or fallback. Invalid policy,
  broken paths, or missing runtime dependencies fail closed. Help does not load
  the runtime, but an unreadable policy location can fail even the help probe.

Workflow files alone do not opt in. The new schema currently supports GitHub;
it does not enable arbitrary providers. See the
[GitHub policy and installation runbook](../tooling/continuous-integration.md#github-actions-opt-in)
before copying the helper into another repository.

The global skill accepts exact command rows only from one unambiguous
`Commands:` section in successful help output. It records the mode before
inspection and does not downgrade after a CI failure. A helper with an
unsupported contract is a stopping condition, not permission to use raw Git.

## Commands

Run feature commands from the feature worktree root and `prepare-main-ci` from
the checked-out guarded main worktree. `prune-evidence` accepts either attached
worktree. Agent skills resolve and quote the authoritative helper path:

```sh
"<main-worktree>/assets/agent-wt-merge" inspect [--fetch] [--json]
"<main-worktree>/assets/agent-wt-merge" prepare-ci [--poll-interval <seconds>] [--poll-timeout <seconds>]
"<main-worktree>/assets/agent-wt-merge" prepare-main-ci [--poll-interval <seconds>] [--poll-timeout <seconds>]
"<main-worktree>/assets/agent-wt-merge" ff --actor opencode|claude [--update-main] [--close-beads <issue-id>]
"<main-worktree>/assets/agent-wt-merge" no-ff --actor opencode|claude -m "<subject>" -m "<body>" [--update-main] [--close-beads <issue-id>]
"<main-worktree>/assets/agent-wt-merge" prune-evidence [--apply] [--json]
```

- `inspect` reports merge facts and cleanup suggestions.
- `prepare-ci` normally publishes the exact current feature SHA, verifies the
  advertised remote feature ref, and polls its configured CI evidence. Defaults are a
  15-second interval and a 15-minute timeout.
- `prepare-main-ci` obtains exact-SHA evidence for a clean, unpushed local main
  tip, including rewritten or batched history, through a reserved temporary CI
  ref with the same polling defaults.
- `ff` runs only `git merge --ff-only` from the main worktree.
- `no-ff` runs only `git merge --no-ff` from the main worktree and uses the
  selected actor for merge commit authorship.
- `prune-evidence` previews GitHub receipt eligibility; `--apply` separately
  authorizes deletion of eligible local metadata, not Git refs or worktrees.

The two preparation commands are available only in CI-gated mode. A direct
attempt in local-only mode fails as unsupported without publishing a ref.

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
If the update changes the mode, provider, or policy evidence contract, it stops
after updating main and before merging the feature. Rediscover capabilities and
approve a new operation; do not retry under the old approval.

Running feature commands from `main` or `master` is an error because there is no
feature worktree to merge. Main accepts only `prepare-main-ci` and, for GitHub,
`prune-evidence`.

The helper never pushes main, another ordinary branch, or tags. `prepare-ci`
publishes only the captured SHA to the same-named remote feature branch without
forcing. `prepare-main-ci` may publish only
`refs/heads/ci/<main>/<full-sha>`. After real CI success, the applicable command
deletes only its owned remote ref with an exact lease. The helper never removes
a local worktree, force-deletes a local branch, or closes a Beads issue without
an explicit issue ID.

## Inspect output

`inspect --json` is the agent-facing interface. It reports:

- current feature branch and SHA;
- `main`/`master` branch and checked-out worktree path;
- whether local main is dirty;
- the remote-tracking main SHA and whether local main is ahead or behind it;
- whether a fetch was attempted and succeeded, with a failure warning;
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
For GitHub, `pipeline` additionally reports `provider`, `policy_fingerprint`,
and `evidence_store`. Inspection validates existing receipts but does not call
the GitHub API or create the store.

## Local-only merges

Both merge commands pin the feature SHA and use the same helper provenance,
clean-main, merge-type, Beads, and local-cleanup checks as CI-gated merges.
They attempt an `origin` fetch best-effort; offline and no-origin repositories
can proceed with a warning. A known behind main still requires explicit
`--update-main` approval and a successful fetch before the update. Diverged
main stops for separate recovery, even with that flag.

Ahead-only main can accept multiple independent feature branches before a
push, using no-ff when fast-forward is impossible. Local-only merges do not
publish features, poll CI, delete remote feature refs, or claim CI success or
bypass. Their reports explicitly mark those operations as not performed.
Agents request approval immediately before the local merge.

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
`ls-remote`, and checks the configured evidence. Missing and active evidence is
retryable; terminal failure, malformed responses, policy errors, and API errors
fail immediately. Timeout leaves the published feature available for diagnosis
and does not change local main. GitHub uses the workflow contract below rather
than the GitLab required-job checker.

The GitLab common-directory `pipeline-guard.override` is an explicit bypass rather
than CI success. Publication is still required, and a bypassed merge retains
the remote feature branch.

Every CI-gated merge repeats a required fetch, pins the feature SHA, verifies
the remote feature advertises that SHA, performs a fresh one-shot CI check, and
revalidates advertised remote main before changing local main. Ahead-only main
can accept another independent branch without pushing between merges. Behind
main requires approved updating; diverged main requires separate recovery. The
merge targets the pinned SHA rather than the movable branch name.

For a no-ff tip directly based on advertised remote main, the push guard checks
the feature parent. If earlier local landings put the merge's first parent
ahead of remote main, pushing the batch requires successful CI for the exact
final main SHA. The merge report identifies this requirement but does not run
main preparation automatically. Feature-job success alone does not cover the
combined batch tip. GitHub single-landing evidence also needs its recorded source
ref and run identity; an arbitrary green run at the same SHA is insufficient.

After real CI success and local merge, the helper deletes the remote feature
with an exact expected-SHA lease and verifies its absence. A missing ref is
already clean. A moved ref is never deleted. Cleanup failure is a post-merge
partial failure: the merge is not rolled back, and Beads closure is still
attempted and reported independently. GitHub first persists the checked identity
and its landing association. If persistence fails after the merge, the feature
ref is retained and the landed SHA, evidence failure, and Beads result are
reported independently. Never rerun the merge to repair that result.

## GitHub workflow evidence

All configured workflows must finish with conclusion `success`. The workflow
owns matrix expansion, conditions, and optional-job semantics; job enumeration
is diagnostic, not a fixed job list or an additional every-job-success gate.
The legacy commit-status rollup is not consulted.

Evidence binds the configured repository, resolved workflow ID, exact SHA,
published branch/ref, `push` event, run ID, and attempt. The helper selects the
newest eligible run, checks attempt-scoped jobs, then revalidates identity and
state before accepting it. Older green runs cannot hide newer failures or
reruns. Missing/active evidence can wait within the preparation timeout; API,
authentication, malformed identity, and terminal failures stop. The helper does
not dispatch or rerun workflows. An already-published SHA may not start a new
run, so reports distinguish publication from reuse.

The optional clone-local `pipeline-guard.githubAccount` selects one stored `gh`
account on the configured host. Without a pin, normal `gh` credential precedence
applies. Missing or unauthorized pinned access fails; the helper never switches
global accounts or tries other logins. Tokens stay inside credential resolution
and API-child environment handling, not arguments, diagnostics, policy, or
receipts. This does not select a Git SSH key, credential helper, or author.
Agents must ask before changing a pin and must not retrieve tokens themselves.

Receipts live at `<git-common-dir>/agent-wt-merge/evidence/v1/`, resolved through
Git rather than by assuming `.git` is a directory. They survive removal of a
linked feature worktree and are shared across human, Claude, and OpenCode
invocations in that clone. They do not live in `.opencode` or `.claude` and are
not copied to independent clones. They contain policy and run references, not
cached authorization. The push guard checks current policy/history and remotely
revalidates the stored identity. Missing receipts can use exact reserved-main
evidence; corrupt or unverifiable metadata requires operator attention, not a
fabricated record or silent downgrade.

## Evidence cleanup

Only an explicit cleanup request enters this workflow. Require the advertised
`prune-evidence` command, then preview with `prune-evidence --json`. Preview may
fetch remote main but deletes nothing. Report its store path and per-record
eligibility reasons, then ask separately before `--apply --json`.

Apply recomputes eligibility against freshly fetched and advertised remote main.
The recorded landing must be included there; policy, local main, and record
contents must remain unchanged, and no active operation may depend on receipts.
Unpushed, unassociated, unverifiable, or concurrently used records remain. A
successful pre-push check is not a successful push. There is no age-only expiry,
automatic post-merge cleanup, or pruning from the push hook.

Only eligible helper-owned receipt files are removed. Metadata locks and active
markers are conservative collision signals; never break them automatically.
An interrupted process may leave one requiring operator investigation. Do not
remove branches, worktrees, remote refs, or GitHub runs as evidence cleanup.

## Exact main CI preparation

Rebasing or cherry-picking a local guarded main after merge changes commit
SHAs, so exact feature-tip evidence no longer covers the rewritten main tip.
Batching independent feature landings can also require evidence for the exact
final main SHA. Finish the intended batch, then separately approve
`prepare-main-ci` from the main worktree if that SHA lacks successful evidence.
Later commits require evidence for the new SHA, even if an earlier tip passed.

For GitLab human ahead-and-behind synchronization, use `gpls`; it records and performs
the rebase before obtaining new evidence and delays finalization until an exact
stash is restored. Humans and agents can use `prepare-main-ci` for a clean main
that is already ahead and not diverged. It:

1. requires a clean, attached guarded main with unpushed commits;
2. fetches the policy remote and rejects behind or diverged history;
3. reuses existing exact-SHA success without publishing a ref;
4. otherwise normally publishes only
   `<main-sha>:refs/heads/ci/<main>/<full-main-sha>` and verifies it;
5. polls the same structured checker every 15 seconds for up to 15 minutes;
6. revalidates the local tip and advertised remote main after success; and
7. exact-lease-deletes the temporary ref only after real success.

For GitHub, reuse must match the reserved `ci/<main>/<full-sha>` push identity,
and the receipt is persisted before temporary-ref deletion. A feature or main
run at the same SHA is not a substitute. GitHub rebase reconciliation is not
part of `gpls` or this helper; use separate recovery before preparing a clean tip.

The command never pushes main or tags; retry the original push separately after
successful preparation. The push guard only checks evidence and never publishes
or waits for CI automatically. Terminal failure, API error, timeout,
override bypass, local or remote movement, and cleanup failure retain the
temporary ref for diagnosis. The full SHA in the namespace prevents collisions,
and an existing reserved ref at a different SHA is never updated.

The managed GitLab-opt-in `pre-rebase` hook protects this evidence earlier.
When a valid GitLab policy is present, it blocks rebasing guarded main while its local
commits are absent from the policy remote-tracking ref. It permits non-main
rebases and guarded main with no local-ahead commits. Invalid guarded state
fails closed. `pipeline-guard.override` does not bypass this check; Git's
explicit `--no-verify` remains a reviewed escape hatch, not a security boundary.

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
Explicit `prepare-ci` and `prepare-main-ci` patterns remain ask-gated because
commit or inspection permission does not authorize publication. In CI-gated
mode, invoking `ff` or `no-ff` authorizes only the helper's lease-protected remote
feature deletion after real CI success. In local-only mode, neither command
mutates remote refs. `prepare-main-ci` authorizes only its temporary CI ref and
eligible exact-lease cleanup. None authorizes pushing main or tags. Local
cleanup commands remain ask-gated. Evidence pruning has separate preview and
apply approval; no new automatic permission rule is implied by its advertisement.
