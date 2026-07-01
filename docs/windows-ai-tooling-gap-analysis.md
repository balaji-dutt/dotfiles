<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Windows AI Tooling Gap Analysis

This document captures the native Windows gaps found while auditing the dotfiles
inventory and AI-tooling setup. It is intended as source material for creating a
Beads Epic and child stories from an environment where `bd` is available, such as
WSL2 or macOS.

## Summary

Claude Code and OpenCode are installed on native Windows, but several operating
assumptions are still macOS/WSL2-centric. The issue is broader than individual
missing tools such as `jq`, TempoGraph, or `bd`: the Windows environment lacks a
clear contract for which AI-agent features are supported natively, which are
WSL2/devcontainer-only, and how those environments stay in sync.

Recommended backlog shape: create an Epic, not a single story.

The Epic should close two classes of drift:

1. **Inventory drift**: documentation does not accurately describe what native
   Windows currently manages, copies, or intentionally ignores.
2. **Runtime drift**: Claude Code, OpenCode, Beads, and related agent workflows
   assume tools or services that are not provisioned on native Windows.

## Evidence gathered

### Current Windows inventory is incomplete

`docs/inventory/windows.md` omits or blurs several native Windows targets:

- `~/.config/opencode/**` is managed on Windows but is not listed.
- `~/AppData/Roaming/yt-dlp/**` is managed on Windows but is not listed.
- Espanso is listed as `match/**` only, but Windows manages `config/**`,
  `match/**`, and `scripts/**`.
- `Documents/PowerShell/**` is described like a direct chezmoi-managed target,
  but it is copied/rendered by `.chezmoiscripts/windows-sync.ps1`.
- `Documents/development/vscode-workspace/**` is also copied by
  `windows-sync.ps1` but is not documented.
- The selected Windows `.chezmoiscripts/` allowlist is not fully documented.

### Starship config is orphaned on native Windows

`%USERPROFILE%\.config\starship.toml` exists and matches
`private_dot_config/starship.toml`, but it is not currently managed on Windows.

Likely history:

- `private_dot_config/starship.toml` was added with the PowerShell prompt work.
- At that time, the Windows `.chezmoiignore` allowed all of `.config/**`, so
  Starship was deployed to Windows.
- Later, the Windows allowlist was tightened to re-allow only selected
  `.config` children, currently including `powershell` and `opencode`.
- The Windows cleanup script removes several stale `.config` trees but does not
  remove `.config\starship.toml`.

Current behavior:

- Starship is installed by the Windows package manifest.
- PowerShell initializes Starship when the executable exists.
- The Starship configuration file is present locally but no longer owned by
  current chezmoi rules.

Recommended policy: manage `~/.config/starship.toml` explicitly on Windows
again, unless there is a deliberate decision to use Starship defaults or remove
the stale file.

### Claude Code has an unprovisioned `jq` dependency

Claude-managed files assume `jq` is available:

- `dot_claude/executable_statusline.sh` hard-requires `jq` to parse Claude
  statusline input and settings. Without `jq`, it falls back to
  `Claude [needs jq]`.
- `dot_claude/hooks/executable_gate-bd-destructive.sh` uses `jq` for PreToolUse
  payload parsing. If `jq` is missing, the hook exits successfully, which means
  the guardrail silently degrades.
- `dot_claude/skills/beads-work/SKILL.md` includes `jq` in Beads workflow
  instructions.

Windows package manifests did not show `jq` as provisioned. Candidate package
sources should be verified before implementation, for example a winget package
such as `jqlang.jq` or the Chocolatey `jq` package.

### TempoGraph is referenced but not provisioned on native Windows

The repo-local OpenCode config defines a local TempoGraph MCP server using:

```jsonc
"mcp": {
  "tempograph": {
    "type": "local",
    "command": ["tempograph-server"]
  }
}
```

The repo instructions tell planning/build agents to use TempoGraph for non-trivial
work. Some Claude agent frontmatter also references `tempograph-server`.

`configs/mise.toml` includes `pipx:tempograph`, but native Windows currently
ignores `.config/mise/**` and no Windows provisioning path for
`tempograph-server` was found.

The backlog needs an explicit policy decision:

- support TempoGraph natively on Windows and provision `tempograph-server`, or
- treat TempoGraph as WSL2/devcontainer-only and gate or remove Windows-native
  references.

### Windows-capable scripts are excluded by the allowlist

The Windows `.chezmoiignore` allowlist permits only selected rendered script
targets. The following PowerShell-capable scripts exist but are currently
ignored on native Windows:

- `claude_mcp_servers.ps1`
- `host_ai_plugin_refresh.ps1`
- `install_plannotator.ps1`

Each script should be reviewed and either explicitly allowed or documented as
intentionally disabled on Windows.

### Beads workflow is unavailable on native Windows

The approved backlog handoff could not be applied from native Windows because
`bd` was unavailable:

- `bd` was not found on `PATH`.
- No `bd.exe` was found in typical install paths.
- The Beads helper script was also absent.

This is part of the broader AI-tooling drift: this repository's agent workflow
expects Beads, but native Windows cannot currently create or update Beads issues.

## Recommended Epic

Title: **Align native Windows AI tooling with dotfiles workflows**

Goal: define and implement the native Windows support contract for Claude Code,
OpenCode, Beads, and related agent tooling, then update the inventory so Windows,
WSL2, and macOS stay understandable and intentionally different where needed.

## Proposed child stories

### Story 1: Reconcile native Windows inventory docs

Scope:

- Update `docs/inventory/windows.md` against actual Windows `chezmoi managed`,
  `chezmoi ignored`, and `windows-sync.ps1` behavior.
- Distinguish direct chezmoi-managed targets from copied/rendered sync outputs.
- Add OpenCode, yt-dlp, full Espanso coverage, Windows sync outputs, and script
  allowlist status.
- Document known unresolved AI-tooling gaps without implying they are already
  fixed.

Acceptance:

- Every major native Windows target class appears in the inventory.
- The inventory no longer implies `Documents/PowerShell/**` is directly managed
  by chezmoi.
- The verification section uses native Windows commands.

### Story 2: Restore explicit Windows ownership of Starship config

Scope:

- Decide whether Windows should manage `~/.config/starship.toml`.
- Recommended implementation: explicitly allow `.config/starship.toml` in the
  Windows `.chezmoiignore` allowlist.
- Update inventory docs to match the chosen policy.

Acceptance:

- `chezmoi managed` on Windows includes `.config/starship.toml`, or the stale
  file is intentionally removed/documented.
- `chezmoi ignored` no longer contradicts the chosen Starship policy.
- `pwsh ./assets/cz-audit.ps1 check .chezmoiignore` passes.
- `chezmoi doctor` has no relevant new finding.

### Story 3: Provision Claude Code runtime dependencies on Windows

Scope:

- Add a Windows provisioning path for `jq`.
- Verify the chosen package source and executable path.
- Document the Claude statusline and hook dependency.

Acceptance:

- A fresh Windows setup installs `jq`.
- `Get-Command jq` and `jq --version` work in PowerShell after PATH repair.
- Claude statusline no longer falls back to `Claude [needs jq]` because of a
  missing executable.
- The Beads destructive-action hook no longer silently degrades solely because
  `jq` is absent.

### Story 4: Define and implement Windows TempoGraph policy

Scope:

- Decide whether TempoGraph is supported on native Windows or only in
  WSL2/devcontainer/macOS contexts.
- If supported, provision `tempograph-server` and verify it is on `PATH`.
- If not supported, gate or remove Windows-native references so OpenCode and
  Claude do not point at a missing executable.

Acceptance:

- The chosen policy is documented.
- OpenCode no longer reports a missing `tempograph-server` on native Windows.
- Claude/OpenCode agent instructions match the chosen policy.

### Story 5: Audit Windows AI automation scripts

Scope:

- Review Windows-capable but currently ignored scripts:
  `claude_mcp_servers.ps1`, `host_ai_plugin_refresh.ps1`, and
  `install_plannotator.ps1`.
- For each script, decide whether it should run on native Windows.
- Update `.chezmoiignore` and docs to match that decision.

Acceptance:

- Every Windows-capable PowerShell chezmoi script is either managed or explicitly
  documented as intentionally disabled.
- Newly enabled scripts pass repo audit for their paths.
- Scripts that touch external services or tokens retain existing guardrails.

### Story 6: Provision Beads CLI for native Windows agent workflows

Scope:

- Decide whether native Windows should support Beads mutations directly.
- If yes, add a reliable provisioning path for `bd` and any helper scripts.
- If no, document that Beads mutations must run from WSL2/macOS and make the
  Windows limitation explicit in agent workflow docs.

Acceptance:

- `bd --version` or equivalent works on native Windows, or the limitation is
  clearly documented.
- Backlog-only Plannotator handoffs have a documented Windows path.
- The workflow does not leave users discovering missing `bd` only after plan
  approval.

### Story 7: Keep AI-tooling environments in sync

Scope:

- Define the support matrix for Claude Code, OpenCode, Beads, TempoGraph,
  Plannotator, and related dependencies across native Windows, WSL2, macOS, and
  devcontainers.
- Identify which manifests own each dependency: winget, Chocolatey, mise,
  Ansible, npm, pipx, or manual install.
- Add a lightweight verification checklist for each platform.

Acceptance:

- Docs identify the owner manifest or deliberate manual step for each AI tool.
- Native Windows differences from WSL2/macOS are explicit rather than accidental.
- Future inventory drift can be caught with documented verification commands.

## Suggested sequencing

1. Restore or explicitly retire Windows Starship ownership.
2. Reconcile Windows inventory docs using the current state plus known gaps.
3. Provision `jq` for Claude Code on Windows.
4. Decide the TempoGraph support policy.
5. Audit and enable or document Windows-capable AI automation scripts.
6. Decide and implement the Beads CLI policy for native Windows.
7. Add the cross-environment AI-tooling support matrix.

## Open decisions

- Should native Windows intentionally manage `~/.config/starship.toml` again?
- Should Windows use winget or Chocolatey for `jq`?
- Should TempoGraph be supported on native Windows or only in WSL2/devcontainers?
- Should native Windows support Beads mutations directly?
- Which currently ignored Windows-capable scripts should run during `chezmoi apply`?
- Where should the long-term AI-tooling support matrix live?

## Beads creation note

When creating issues from this document, create the Epic first and link the child
stories to it. The stories are separable by risk and verification surface; do not
collapse them into one implementation task unless the scope is reduced to a
read-only documentation update.
