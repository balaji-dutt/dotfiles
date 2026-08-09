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

`bd dolt pull` cannot succeed in this repo — `bd` dirties an ignored table inside
its own pull path and then fails to merge on it. Use the helper instead. It is
the only `bd` command that is replaced; every other `bd` operation is unaffected.

### macOS / Linux / WSL2

```sh
./assets/beads-sync.sh status
./assets/beads-sync.sh pull
```

### Windows (PowerShell 7)

```powershell
pwsh ./assets/beads-sync.ps1 status
pwsh ./assets/beads-sync.ps1 pull
```

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
