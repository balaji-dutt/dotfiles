<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Continuous Integration

GitLab CI provides a clean Linux check for short-lived feature branches and an
opt-in native Windows lane. It is not a multi-device file-distribution system:
Git remains responsible for moving commits between devices, while CI verifies a
pushed commit independently of the development host.

The policy favors useful feedback within the GitLab Free compute allowance.
Only the `fast` Linux suite is automatic. Full Linux, Windows, and disposable
devcontainer runs are manual, and superseded automatic jobs are interruptible.

## Agent worktree workflow

OpenCode and Claude use global post-commit adapters that remain inactive unless
the current non-main repository has an executable `agent-wt-merge` helper and a
valid pipeline policy. A successful `oc-commit` or `cc-commit` that changes HEAD
injects guidance for the active agent; it does not publish anything.

The steps below apply to this repository's GitLab-policy mode. A copied helper
without `configs/gitlab-pipeline-guard.json` advertises only `inspect`, `ff`, and
`no-ff`, works without the GitLab runtime, and merges locally with best-effort
fetching. It does not publish features or operate another provider's pipelines.
The global skill follows the selected helper's `Commands:` rows, not incidental
mentions of CI commands in its help text. Broken configured CI fails closed.

1. Develop and commit on a short-lived feature branch.
2. Separately approve publication and monitoring:

   ```sh
   "<main-worktree>/assets/agent-wt-merge" prepare-ci
   ```

   The helper captures the feature tip, performs a normal exact-SHA push to the
   same-named remote feature branch, verifies the advertisement, and polls
   `linux-fast` every 15 seconds for up to 15 minutes. It never pushes main,
   another branch, or tags.
3. After exact-SHA success, use `agent-wt-merge inspect` and the selected `ff`
   or `no-ff` command. The merge command fetches and rechecks publication and CI
   before changing local main.
4. After a real CI-backed merge, the helper exact-lease-deletes the remote
   feature branch before Beads closure. A bypass retains it. Local worktree and
   feature-branch cleanup remains separately permissioned.
5. Additional independent features may land on ahead-only local main without
   pushing between merges. Finish the batch before preparing its final SHA.
6. If the helper or push guard requires exact main-tip evidence, separately run
   `agent-wt-merge prepare-main-ci` from main before pushing.
   A no-ff tip directly based on advertised remote main can use feature-parent
   evidence; a two-parent batch tip requires its own successful job. Main
   preparation never pushes main or tags.
7. Push main, or retry the original push if it was blocked. The managed
   `pre-push` hook verifies the required SHA before Git sends the separately
   requested update.

Do not rebase or pull with rebase after the local merge and before the guarded
main push. Replaying the commit changes its SHA and invalidates exact-SHA
evidence. The managed repository-opt-in `pre-rebase` hook blocks this operation
when guarded main has commits that are absent from its policy remote-tracking
ref. It does not block non-main rebases or a main branch with no local-ahead
commits.

The OpenCode plugin and Claude hook are global adapters with repository-scoped
activation. They validate the local policy and derive the required job and
helper path from the current repository. They inspect local Git and inject
context only; they never call GitLab, push, merge, or delete refs. Restart
OpenCode after installing or changing its plugin because plugins are loaded at
session startup.

## Manual feature workflow

For a non-agent session, the equivalent publication step is:

```sh
git push -u origin HEAD
```

Wait for the exact commit's `linux-fast` job before using the merge helper. Do
not use a manual merge when the repository policy exists but the authoritative
helper is unavailable; that would omit the fresh CI, publication, remote-main,
and lease-cleanup checks.

## Preparing a batch for push

`agent-wt-merge` is also a human-facing command despite its name. After landing
several independent branches, local main may have a combined tip whose SHA has
never run CI. When the merge report or push guard identifies that SHA, finish
the intended batch and run from the clean, ahead-only main worktree:

```sh
./assets/agent-wt-merge prepare-main-ci
```

For an agent, this publication needs separate approval. The command reuses
successful exact-SHA evidence when available; otherwise it publishes only
`refs/heads/ci/main/<full-sha>`, polls `linux-fast`, and exact-lease-deletes the
temporary ref after real success and tip revalidation. Failure, timeout, bypass,
movement, or cleanup failure retains any published ref for diagnosis.

Then retry the original push separately, including its originally intended
flags such as `--follow-tags`. Neither the merge helper nor the push guard
pushes main or tags automatically. The guard does not create a CI ref or wait
for a job. Additional commits require successful evidence for the new SHA;
feature-job success or an earlier main result does not cover a changed batch.

## Rewritten main recovery

When guarded local main has unpushed commits and `origin/main` advances, use the
normal human pull alias from the main worktree:

```sh
gpls
```

`gpls` keeps its existing `git pull --rebase` behavior in generic repositories,
on non-guarded branches, and when guarded main is not both ahead and behind its
policy remote. For that exact divergence, it delegates to
`assets/guarded-main-sync`. Dirty worktrees are stashed first and identified by
their exact stash object ID.

The helper records the original tips in the Git common directory, rebases onto
the pinned advertised remote tip, and checks the rewritten SHA. It uses a
controlled internal `git rebase --no-verify`; this does not weaken the normal
`pre-rebase` hook or require a manual bypass. If evidence is absent, it publishes
only `<sha>:refs/heads/ci/main/<full-sha>` and polls for `linux-fast`. After real
success it revalidates local and remote state and exact-lease-deletes only an
unchanged temporary ref. Final recovery state is removed only after any `gpls`
stash is restored and dropped.

Rebase conflict, CI failure, timeout, override, remote movement, stash-restore
failure, and cleanup failure preserve recovery state and any applicable stash or
evidence ref. Inspect it with `./assets/guarded-main-sync status`, then use the
reported `resume`, `finalize`, or `abort` action. The helper never pushes main or
tags. After success, run the reported command separately:

```sh
git push
```

Humans and agents can also use `agent-wt-merge prepare-main-ci` for a clean
rewritten main that is already ahead and not diverged, with the same preparation
contract as a batch tip. Both paths obtain fresh exact-SHA evidence; patch
equivalence never reuses the original evidence.

When a branch has an open merge request, its MR pipeline owns `linux-fast`; the
parallel branch-push pipeline does not run a duplicate job. A `main` push also
does not automatically rerun `linux-fast`. Existing sync jobs continue to run on
`main` under their original rules.

The first merge that introduces a managed hook is a bootstrap exception: its
source copy is not installed into existing repositories until the Git-template
hook sync runs during a later `chezmoi apply`. Use the existing feature
publication and exact-SHA merge gate for that change; do not apply dotfiles
merely to make a source change testable.

## Pipeline rule table

| Pipeline context | `linux-fast` | Manual full and devcontainer jobs |
| --- | --- | --- |
| Feature push without an open MR | automatic, gating | manual, non-blocking |
| Feature push with an open MR | skipped; MR owns the SHA | manual, non-blocking |
| Non-Renovate merge request | automatic, gating | manual, non-blocking |
| `renovate/*` push or MR source | skipped | skipped |
| MR carrying the exact `renovate` label | skipped | skipped |
| `main` branch push | absent | manual, non-blocking |
| Scheduled pipeline | absent | manual, non-blocking |
| Web pipeline | absent | manual, non-blocking |
| Tag, API, trigger, or other source | absent | absent |

GitLab evaluates `rules` in order. Branch namespace is the primary Renovate
signal; the comma-delimited MR label is secondary. The targeted statusline,
Beads Kanban pin, and browser-policy sync jobs are separate and retain their
existing MR/push behavior, including on their Renovate branches.

## Jobs and artifacts

`linux-fast` and `linux-all` use the pinned full Debian image
`python:3.13.7-bookworm`. They run without package installation:

```sh
./assets/run-tests.sh fast \
  --require-capability git \
  --require-capability sh \
  --report-file ci-artifacts/linux-fast.json

./assets/run-tests.sh all \
  --require-capability bash \
  --require-capability git \
  --require-capability sh \
  --report-file ci-artifacts/linux-all.json
```

`windows-all` is a manual job on GitLab's current Windows hosted-runner beta:

```powershell
pwsh -NoProfile -File ./assets/run-tests.ps1 all `
  --require-capability git `
  --require-capability pwsh `
  --report-file ci-artifacts/windows-all.json
```

The runner tag is `saas-windows-medium-amd64`; the job deliberately has no
container `image` because the hosted Windows executor does not support one. If
GitLab changes beta or subscription availability, a self-hosted Windows runner
can reuse the same command and capability contract.

`devcontainer-smoke` is a manual, non-blocking job that requires a self-hosted
runner carrying the `devcontainer-smoke` tag, Docker, the Dev Container CLI,
Python 3, and the preloaded `homelab-iac:base` image. It does not use a job image
or pull one:

```sh
DEVCONTAINER_SMOKE=1 \
DEVCONTAINER_SMOKE_ARTIFACTS=ci-artifacts/devcontainer-smoke \
python3 assets/devcontainer-smoke.py --run
```

The job remains pending when no matching runner is provisioned; its presence is
not evidence that a smoke run occurred. A run uses only repository sources,
synthetic temporary inputs, and a unique persistent volume. Docker networking is
disabled, commands are bounded, and cleanup plus diagnostics run after failures.

Artifacts are uploaded even when tests fail and expire after seven days. Their
JSON records selected steps, registered `covers` paths, skips, failures, and the
overall exit code. This is suite-registration coverage, not line or branch
coverage. The automatic fast suite also enforces the automation inventory's
classification, risk, and critical-behavior declarations; see
`docs/tooling/automation-coverage-policy.md`.

No CI cache is configured. The current suites use repository and standard-library
inputs and have no dependency-download phase, so caching would add stale-state
risk without reducing setup work. Default test jobs require no credentials,
production Beads database, or live network.

## Main-push guard

`configs/gitlab-pipeline-guard.json` opts this repository into the managed
`pre-push` hook. The standard-library helper queries the public GitLab API for
project `44618209` and requires an exact successful `linux-fast` job.

- A linear `main` tip checks the pushed tip SHA.
- A normal no-ff merge whose first parent is the advertised remote `main` checks
  its second, feature-parent SHA.
- Another two-parent tip checks the exact pushed main SHA, provided advertised
  remote main is its ancestor. This covers several local landings in one push.
- Non-fast-forward, octopus, missing-object, and unsupported histories fail
  rather than selecting a commit heuristically.

The single-merge feature-parent policy does not test changes made only while
resolving that merge. Push the feature branch again after amending its tip, or
use a manual full lane when integrated-tree behavior needs validation. Batch-tip
validation checks the integrated main commit rather than substituting success
from either feature parent.

The check fails closed when the pipeline is absent, pending, failed, or
unreachable. A non-successful main-tip job reports the required SHA and points
to explicit `prepare-main-ci` from main, followed by a separate retry of the
original push. Failed jobs still require investigation; preparation does not
turn failure into success. The helper uses no token because the project is
public; making it private requires a separate authentication design rather than
silently weakening the guard.

The same checker exposes a machine-readable exact-SHA mode to the shared
pipeline runtime used by `agent-wt-merge` and `guarded-main-sync`. Its outcomes
are `success`, `retryable`, `terminal`, `error`, and `bypass`. Only a missing or
active required job is retryable. The pre-push arguments and stdin protocol do
not authorize publication or polling; the guard performs one evidence check
per selected SHA.

For a deliberate temporary bypass, create the fixed override in the Git common
directory. It applies to all linked worktrees and remains active until removed:

```sh
override="$(git rev-parse --path-format=absolute --git-common-dir)/pipeline-guard.override"
touch "$override"
# perform the reviewed exceptional push
rm -f "$override"
```

```powershell
$common = git rev-parse --path-format=absolute --git-common-dir
$override = Join-Path $common 'pipeline-guard.override'
New-Item -ItemType File -Force $override | Out-Null
# perform the reviewed exceptional push
Remove-Item -Force $override
```

Every bypassed guarded push prints a warning. `git push --no-verify` is Git's
own broader escape hatch; neither mechanism is a security boundary.

The common-directory pipeline override does not authorize rebasing guarded
local main. Git's explicit `git rebase --no-verify` remains a reviewed escape
hatch because hooks are not a security boundary. A resulting rewritten main
must obtain fresh evidence through guarded `gpls` reconciliation or
`prepare-main-ci`; patch equivalence does not preserve exact-SHA
evidence.

For worktree merges, bypass does not count as CI success. The feature must still
be published at the exact SHA, and the remote feature ref is retained after the
local merge.

## Platform matrix and gaps

| Platform | CI status | Canonical command | Requirement or gap |
| --- | --- | --- | --- |
| Linux | automatic fast; manual all | `./assets/run-tests.sh fast` / `all` | GitLab Debian hosted runner |
| WSL2 | represented by Linux, not native | `./assets/run-tests.sh all` | no hosted WSL2 runner; Linux does not prove `op.exe` versus `op` selection |
| Native Windows | manual all | `pwsh -NoProfile -File ./assets/run-tests.ps1 all` | current Windows hosted-runner beta or equivalent self-hosted tag |
| macOS | local only | `./assets/run-tests.sh all` | hosted macOS is not available on the current Free plan |
| Devcontainer | manual, non-blocking when a tagged runner exists | `DEVCONTAINER_SMOKE=1 python3 assets/devcontainer-smoke.py --run` | requires a self-hosted `devcontainer-smoke` runner with the preloaded image; otherwise the registered live step skips locally |

Missing optional tools remain explicit runner skips unless a lane names them
with `--require-capability`. A platform is not reported as passing when no runner
has executed its command.
