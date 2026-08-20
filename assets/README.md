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
