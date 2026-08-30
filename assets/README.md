<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# assets/

This directory contains repo-only helper scripts for this repository.

Detailed audit documentation lives in `docs/tooling/cz-audit.md`. The
agent-facing worktree merge helper is documented in
`docs/agents/worktree-merge-helper.md`.

## Common Commands

Run from the repository root.

### Browser policy sync

```sh
python3 assets/sync-browser-policies.py --check
python3 assets/sync-browser-policies.py --write
```

No mode flag is equivalent to `--write`.

### Better Beads Kanban pin sync

Renovate bumps only the version sentinel in the three VSIX install sites; the
tag and asset name are derived from it inside each script, and the release
checksum is the one value Renovate cannot compute.

```sh
./assets/sync-beads-kanban-pin.sh --check
./assets/sync-beads-kanban-pin.sh --write
```

No mode flag is equivalent to `--write`. Both modes read the pinned version from
the two `run_onchange_after_install_better_beads_kanban.*.tmpl` scripts and the
homelab-IaC `devcontainer-common.sh`, fail if the three disagree, then
resolve the checksum from the release's `SHA256SUMS` asset (falling back to
hashing the VSIX). CI runs `--write` on `renovate/beads-kanban-*` branches and
`--check` everywhere else — see
`docs/automation/renovate-gitlab-runner-setup.md`.

### Beads release-note check

Before a core Beads client update can automerge, compare the host and
devcontainer pins and inspect every newer upstream release for incident
language:

```sh
python3 assets/check-beads-release-notes.py
```

The helper requires the `beads_version` pin in `.chezmoidata.yaml` and the
`@beads/bd` pin in the homelab-IaC `npm_packages.txt` file to be identical. It
then queries the public GitHub releases API and fails closed on input, API, or
rate-limit errors. Set `GITHUB_TOKEN` or `GH_TOKEN` when an authenticated API
request is needed; unauthenticated requests are supported.

For deterministic local testing, provide a GitHub releases JSON array without
contacting the network:

```sh
python3 assets/check-beads-release-notes.py \
  --releases-file <path-to-releases.json>
```

The fixture path is an input seam; the repository does not keep a live release
snapshot. Exit status 0 is clean, 1 means newer release notes contain an
incident term that needs review, and 2 means the pins or release data could not
be trusted. CI runs this check only for `renovate/beads-core-*` push and merge
request pipelines. Better Beads Kanban uses the separate
`renovate/beads-kanban-*` dispatch.

### Plannotator slash-command sync

Plannotator's slash commands ship as files upstream installs outside chezmoi:
`scripts/install.sh` copies the Claude skills into `~/.claude/skills`, and the
`@plannotator/opencode` package's `postinstall` drops its command stubs into
`~/.config/opencode/commands`. This repo runs neither, so the six files are
vendored and applied by chezmoi instead.

```sh
python3 assets/sync-plannotator-assets.py --check
python3 assets/sync-plannotator-assets.py --write
```

No mode flag is equivalent to `--write`. Both modes read
`configs/plannotator-assets.json` and fetch from the tag matching
`plannotator_version` in `.chezmoidata.yaml`; `--check` reports drift without
writing. After `--write`, run `bash ./assets/sync-devcontainer-assets.sh` to
mirror the Claude skills into container-dotfiles.

See `docs/plannotator.md` for why the upstream installer is bypassed.

### AI tooling drift check

Checks active MCP runtime declarations against the support matrix without
changing files or contacting external services:

```sh
python3 assets/check-ai-tooling.py
```

```powershell
py -3 assets/check-ai-tooling.py
```

See `docs/inventory/ai-tooling.md` for the scan boundary, update checklist, and
native-Windows preflight.

### Automation test inventory

Checks that every tracked automation candidate has exactly one ownership and
test-suite classification, without changing files or contacting external
services:

```sh
python3 assets/check-automation-test-inventory.py
```

```powershell
py -3 assets/check-automation-test-inventory.py
```

Use `--list-candidates` to review the tracked census and replacement digest.
See `docs/inventory/automation-testing.md` for discovery rules, classifications,
and the update workflow. Risk tiers, critical branch matrices, and new-script
review criteria are documented in `docs/tooling/automation-coverage-policy.md`.

### Automation provenance

Checks generated outputs, tracked devcontainer mirrors, derived Promptfoo pins,
Espanso render chains, statusline copies, and upstream-derived unslop copies
without changing files or contacting external services:

```sh
python3 assets/check-automation-provenance.py
```

```powershell
py -3 assets/check-automation-provenance.py
```

See `docs/inventory/automation-provenance.md` for authorities, accepted
divergences, offline boundaries, and the update workflow.

### Test suites

Runs repository tests through the same isolated suite contract on POSIX and
PowerShell hosts. No suite argument selects the fast suite:

```sh
./assets/run-tests.sh
./assets/run-tests.sh integration
./assets/run-tests.sh --list all
```

```powershell
pwsh -NoProfile -File ./assets/run-tests.ps1
pwsh -NoProfile -File ./assets/run-tests.ps1 integration
pwsh -NoProfile -File ./assets/run-tests.ps1 --list all
```

Available suites are `fast`, `integration`, `render`, `provenance`, `platform`,
and `all`. See `docs/tooling/test-runner.md` for capability skips, reports, exit
codes, suite registration, and fixture contracts. GitLab jobs and the guarded
feature-push workflow are documented in `docs/tooling/continuous-integration.md`.

### Disposable devcontainer smoke

Runs the production persistence and materialization phases in a disposable
container with synthetic read-only inputs and a uniquely named volume. The
required image must already exist; the harness disables pulls and networking:

```sh
DEVCONTAINER_SMOKE=1 python3 assets/devcontainer-smoke.py --probe
DEVCONTAINER_SMOKE=1 python3 assets/devcontainer-smoke.py --run
```

The probe creates no containers, volumes, or temporary files. The live run is
POSIX-only, has bounded commands, always removes its owned container and volume,
and writes diagnostics under `ci-artifacts/devcontainer-smoke/`.

### Claude MCP registration

Registers the user-scope Claude MCP servers declared in `configs/claude-mcp.json`.
Normally invoked for you — by the chezmoi hook
`run_onchange_after_claude_mcp_servers.sh.tmpl` on macOS/Linux/WSL2 applies, and
by the homelab-IaC devcontainer over its read-only `/tmp/host-dotfiles` mount.
Run it by hand to preview or reapply:

```sh
CLAUDE_MCP_DRY_RUN=1 python3 assets/claude-mcp-apply.py configs/claude-mcp.json
python3 assets/claude-mcp-apply.py configs/claude-mcp.json
```

Windows has a separate PowerShell implementation inside
`.chezmoiscripts/run_onchange_after_claude_mcp_servers.ps1.tmpl`. See
`docs/automation/claude-mcp.md`.

### Existing Git hook sync

`git-template-hook-sync.py` copies the hooks from the managed Git template into
existing repositories. The POSIX and Windows `20-git-template-hooks` apply
hooks invoke it with the standard development roots on every `chezmoi apply`.

The helper stores content-hash ownership beside each repository's Git hooks. It
updates or removes only unchanged owned copies, adopts byte-identical hooks,
and preserves unknown or unrelated hooks. Valid custom `core.hooksPath` values
are reported and left alone.

Repositories rejected by Git's `safe.directory` ownership check are skipped
with an informational message. Trust must be granted deliberately through Git
configuration; the helper never weakens or bypasses that check.

This repository also opts into managed `pre-push` and `pre-rebase` checks. The
first requires a successful GitLab `linux-fast` job before `origin/main` is
pushed; the second blocks rewriting guarded local main while it has unpushed
commits. Both are no-ops in repositories without
`configs/gitlab-pipeline-guard.json`; see
`docs/tooling/continuous-integration.md` for normal use and explicit escape
hatches. Agent worktree sessions use `agent-wt-merge prepare-ci` before merging.
For humans, `gpls` delegates to `guarded-main-sync` only when policy-guarded main
is both ahead and behind its remote. It performs a recorded rebase, publishes
and monitors only the rewritten exact tip on a reserved temporary ref, restores
an exact dirty-worktree stash, and reports the separate final `git push`.
`agent-wt-merge prepare-main-ci` remains the agent-facing compatibility command
for a clean rewritten main that is already ahead and not diverged. None of these
commands pushes main or tags.

For a targeted reconciliation, pass the template directory and one or more
roots explicitly:

```sh
python3 assets/git-template-hook-sync.py \
  --template-hooks-dir "$HOME/.config/git/template/hooks" \
  --repo-root "$HOME/Documents/development"
```

### macOS / Linux / WSL2

```sh
./assets/cz-audit.sh check <repo-relative-path>
```

### Windows (PowerShell 7)

```powershell
pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
```

### Examples

```sh
./assets/cz-audit.sh check dot_bashrc
./assets/cz-audit.sh check .chezmoiignore
./assets/cz-audit.sh check .chezmoi.toml.tmpl
./assets/cz-audit.sh check ansible/site.yml
pwsh ./assets/cz-audit.ps1 check bootstrap-wsl.sh
```

## Beads sync

The interactive shell wrappers redirect exact `bd dolt pull` and `bd dolt push`
commands to the helper. Pull avoids the dirty-table merge bug; push verifies a
Dolt remote before doing any push work. Every other `bd` operation is unaffected.

Agents bypass the interactive wrapper with `command bd` on POSIX or `bd.exe` on
native Windows. They must invoke the helper explicitly when synchronization is
intended rather than sourcing shell startup files.

### macOS / Linux / WSL2

With direnv loaded inside this repository, `assets/` is on `PATH`, so an
interactive shell can use the extensionless launcher:

```sh
beads-sync status
beads-sync pull
beads-sync push
```

Without direnv, including in agent and non-interactive shells, keep using the
explicit entrypoint (for example, `./assets/beads-sync.sh status`).

### Windows peer mode (PowerShell 7)

These commands apply only to a Windows checkout that owns a local Dolt
database:

```powershell
pwsh -NoProfile -File ./assets/beads-sync.ps1 status
pwsh -NoProfile -File ./assets/beads-sync.ps1 pull
pwsh -NoProfile -File ./assets/beads-sync.ps1 push
```

This repository's native Windows checkout runs in client mode and deliberately
has no local Dolt database. The PowerShell helper therefore refuses these
commands and prints the corresponding WSL2-host command; run
`./assets/beads-sync.sh` from the WSL2 checkout instead.

Commands are `status`, `clean`, `pull`, `push`, `init`; both accept a dry-run
and a backup flag (`init` rejects backup — there is no database to export at
that point). The helper refuses to reset any table that is not listed in
`dolt_ignore`. `init` rebuilds a wedged peer from the sync remote after
`.beads/dolt` has been moved aside — see the Recovery section of
`docs/beads.md`.

## Agent Worktree Merges

Agents landing a feature worktree should use the repo-local helper instead of
generating ad hoc shell or Python snippets. The agent skills invoke the copy
from the checked-out main worktree while keeping the feature worktree as the
current directory. A manual feature-local invocation delegates to that main
copy when available:

```sh
./assets/agent-wt-merge inspect --fetch --json
./assets/agent-wt-merge ff --actor opencode
```

See `docs/agents/worktree-merge-helper.md` for the full workflow and safety
rules.
