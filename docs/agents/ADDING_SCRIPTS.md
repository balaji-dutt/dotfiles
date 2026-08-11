<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Standards

- Use existing code style conventions and patterns.

There are two classes of script in this repo. The rules below apply to the first.

## Chezmoi-applied scripts

Scripts that run as part of `chezmoi apply` to install or configure the machine.

- Scripts must always be created in the `.chezmoiscripts` folder and nowhere else.
- Scripts must always use `chezmoi` templates and have logic that makes the execution logic conditional on which operating system it is running under.

## Repo-only helpers

Tooling invoked by hand from the repository root, never applied to a target and
never templated. These live in `assets/` and are paired per platform (`*.sh` for
macOS/Linux/WSL2, `*.ps1` for PowerShell 7) rather than using chezmoi's
OS conditionals.

A single cross-platform Python helper is an accepted alternative to the `*.sh` /
`*.ps1` pair when both platforms would otherwise run identical logic, or when a
chezmoi-applied script and something outside chezmoi (a devcontainer lifecycle
hook, for example) must share one implementation. `sync-browser-policies.py` and
`claude-mcp-apply.py` follow this shape; the latter is invoked both by the
chezmoi hook `run_onchange_after_claude_mcp_servers.sh.tmpl` and by the
homelab-IaC devcontainer over its read-only host mount. Such helpers still obey
the rules above — repo-only, not templated, not applied to a target.

Existing members: `cz-audit.sh` / `cz-audit.ps1`, `beads-sync.sh` /
`beads-sync.ps1` and the repo-local `beads-sync` launcher, `agent-wt-merge`,
`claude-mcp-apply.py`, `sync-browser-policies.py`, and the `sync-*` scripts.
See `assets/README.md` for their usage.
