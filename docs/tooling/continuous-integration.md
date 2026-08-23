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
Only the `fast` Linux suite is automatic. Full Linux and Windows runs are manual,
and superseded automatic jobs are interruptible.

## Local feature workflow

1. Develop and commit on a short-lived local feature branch.
2. Push that branch, even when no merge request is needed:

   ```sh
   git push -u origin HEAD
   ```

3. Wait for the exact commit's `linux-fast` job to pass.
4. Merge the branch into local `main`.
5. Push `main`. The managed `pre-push` hook verifies the feature-tip pipeline
   before Git sends the update.

When a branch has an open merge request, its MR pipeline owns `linux-fast`; the
parallel branch-push pipeline does not run a duplicate job. A `main` push also
does not automatically rerun `linux-fast`. Existing sync jobs continue to run on
`main` under their original rules.

The first merge that introduces the hook is a bootstrap exception: the managed
copy is not installed into existing repositories until the Git-template hook
sync runs during a later `chezmoi apply`. Push this feature branch, wait for its
`linux-fast` job, and verify that result manually before the first local merge
and `main` push. Do not apply dotfiles merely to make a source change testable.

## Pipeline rule table

| Pipeline context | `linux-fast` | `linux-all` / `windows-all` |
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

- A fast-forward/linear `main` update checks the pushed tip SHA.
- A normal no-ff merge whose first parent is the advertised remote `main` checks
  its second, feature-parent SHA.
- Non-fast-forward, octopus, missing-object, and ambiguous histories fail rather
  than selecting a commit heuristically.

The selected feature-tip policy permits normal local no-ff merges but does not
test changes made only while resolving that merge. Push the feature branch again
after amending its tip, or use a manual full lane when integrated-tree behavior
needs validation.

The check fails closed when the pipeline is absent, pending, failed, or
unreachable. Wait for GitLab and retry the push. The helper uses no token because
the project is public; making it private requires a separate authentication
design rather than silently weakening the guard.

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

## Platform matrix and gaps

| Platform | CI status | Canonical command | Requirement or gap |
| --- | --- | --- | --- |
| Linux | automatic fast; manual all | `./assets/run-tests.sh fast` / `all` | GitLab Debian hosted runner |
| WSL2 | represented by Linux, not native | `./assets/run-tests.sh all` | no hosted WSL2 runner; Linux does not prove `op.exe` versus `op` selection |
| Native Windows | manual all | `pwsh -NoProfile -File ./assets/run-tests.ps1 all` | current Windows hosted-runner beta or equivalent self-hosted tag |
| macOS | local only | `./assets/run-tests.sh all` | hosted macOS is not available on the current Free plan |
| Devcontainer | documented command only | `./assets/run-tests.sh all` inside a disposable container | no repo-owned runner yet; smoke implementation is `dots-4jy.10.6.4` |

Missing optional tools remain explicit runner skips unless a lane names them
with `--require-capability`. A platform is not reported as passing when no runner
has executed its command.
