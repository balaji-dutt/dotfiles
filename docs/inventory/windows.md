<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Inventory: Windows (PowerShell)

This page records the native Windows surface produced by the current source
state and `.chezmoiignore` rules.

## Ownership Terms

The Windows setup has three distinct ownership classes:

- **Direct managed targets** are rendered or copied into the home directory and
  appear under `chezmoi managed`.
- **Apply hooks** appear under `.chezmoiscripts/` in the managed inventory, but
  they execute behavior instead of installing persistent files at those paths.
- **Sync outputs** are copied or rendered by the `windows-sync.ps1` apply hook.
  Their destination paths are not owned by chezmoi and do not appear under
  `chezmoi managed`.

## Direct Managed Targets

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| Git | `~/.gitconfig`, `~/.gitignore_global` | `dot_gitconfig.tmpl`, `dot_gitignore_global.tmpl` |
| Git template hooks | `~/.config/git/template/hooks/**` | `private_dot_config/git/template/hooks/**` |
| Markdownlint | `~/.markdownlint-cli2.jsonc` | `dot_markdownlint-cli2.jsonc` |
| Starship | `~/.config/starship.toml` | `private_dot_config/starship.toml` |
| Claude | `~/.claude/**` | `dot_claude/**` |
| OpenCode | `~/.config/opencode/**` | `private_dot_config/opencode/**` |
| PowerShell modules | `~/.config/powershell/*.ps1` | `private_dot_config/powershell/*.ps1.tmpl` |
| Sublime Merge | `~/AppData/Roaming/Sublime Merge/Packages/**` | `AppData/Roaming/Sublime Merge/Packages/**` |
| Espanso | `~/AppData/Roaming/espanso/{config,match,scripts}/**` | `AppData/Roaming/espanso/**`, `.chezmoitemplates/espanso/**`, `configs/espanso/**` |
| yt-dlp | `~/AppData/Roaming/yt-dlp/{config,portable-playlist,portable-video}` | `AppData/Roaming/yt-dlp/**` |

Starship is explicitly allowlisted on Windows because the managed config backs
the Starship initialization in `~/.config/powershell/prompt.ps1`.

`Documents/PowerShell/**` is deliberately absent from this table. It is a sync
output, not a direct managed target. Browser policy registry keys are apply-hook
side effects for the same reason.

## Windows Sync Outputs

`.chezmoiscripts/run_after_windows-sync.ps1.tmpl` runs after each Windows
apply. It currently processes these source-to-destination mappings:

| Source | Destination | Current Output |
| :--- | :--- | :--- |
| `private_Documents/PowerShell/**` | The Windows Documents `PowerShell` directory, or `data.windows.powershell_dir` when configured | `Microsoft.PowerShell_profile.ps1`, `Scripts/MapDrives.ps1`, `Scripts/Start-WslSshPageant.ps1` |
| `private_Documents/development/vscode-workspace/**` | `F:\Balaji\Development\vscode-workspace` | `container-homelab-IaC.code-workspace`, rendered `chezmoi-dotfiles.code-workspace` |

For each mapping, the hook translates chezmoi component prefixes such as
`private_` and `executable_`. It renders `.tmpl` sources, removes the template
suffix, and copies non-template files. A missing source root is skipped.

The operation does not register destination files as managed targets and does
not prune arbitrary orphan files inside a destination tree. Separately, the
hook removes stale `.emacs.d`, `.config/emacs`, `.config/doom`,
`.config/lazygit`, and `.config/sublime-merge` directories from the Windows
home directory.

## Windows Apply-Hook Allowlist

Windows ignores `.chezmoiscripts/**` by default and then admits these eleven
rendered hook targets:

| Managed Hook Target | Source Template | Trigger | Purpose |
| :--- | :--- | :--- | :--- |
| `10-dotfiles-commit-template.ps1` | `run_after_10-dotfiles-commit-template.ps1.tmpl` | after | Configure this repo's commit template and managed Git hooks path |
| `99-cleanup-wrong-apply.ps1` | `run_once_after_99-cleanup-wrong-apply.ps1.tmpl` | once, after | Remove curated repo and non-Windows paths from the Windows home directory |
| `browser-policies.ps1` | `run_onchange_after_browser-policies.ps1.tmpl` | onchange, after | Import enabled Chrome and Firefox registry policies |
| `claude_mcp_servers.ps1` | `run_onchange_after_claude_mcp_servers.ps1.tmpl` | onchange, after | Reconcile declarative user-scope Claude MCP registrations |
| `copy_sublime_merge_packages.ps1` | `run_once_before_copy_sublime_merge_packages.ps1.tmpl` | once, before | Install the Sublime Merge Git commit syntax files |
| `install_beads_kanban_bd_fixes.ps1` | `run_onchange_after_install_beads_kanban_bd_fixes.ps1.tmpl` | onchange, after | Install the pinned Beads Kanban VSIX fork when VS Code is available |
| `install_codebase-memory-mcp.ps1` | `run_onchange_after_install_codebase-memory-mcp.ps1.tmpl` | onchange, after | Install the pinned standard codebase-memory-mcp Windows binary |
| `install_plannotator.ps1` | `run_onchange_after_install_plannotator.ps1.tmpl` | onchange, after | Install the pinned native Plannotator CLI binary |
| `windows-bootstrap.ps1` | `run_onchange_after_windows-bootstrap.ps1.tmpl` | onchange, after | Reconcile selected user PATH entries and PowerShell profile loading |
| `windows-sync.ps1` | `run_after_windows-sync.ps1.tmpl` | after | Render or copy the sync outputs documented above |
| `windows-zz-register-startup-tasks.ps1` | `run_after_windows-zz-register-startup-tasks.ps1.tmpl` | after | Register `Start-WslSshPageant` at logon, with a Startup-folder fallback |

The browser-policy hook imports registry files only when the feature is enabled.
A non-elevated apply skips the import with a command for running it separately
as administrator. The startup hook prefers a per-user scheduled task and writes
`Start-WslSshPageant.vbs` to the Startup folder only when task registration is
denied.

## codebase-memory-mcp

Native Windows installs the Renovate-pinned standard codebase-memory-mcp
release for amd64 or arm64 through the admitted onchange hook. The hook requires
the matching entry in the release `checksums.txt`, verifies the archive with
SHA-256, validates the candidate version, and publishes only
`~/.local/codebase-memory-mcp.exe`. It does not use mise, Winget, the upstream
installer, or the upstream `install` command, so it does not rewrite MCP client
configuration.

`windows-bootstrap.ps1` owns `~/.local` in the persistent user `PATH`. Restart
OpenCode, Claude Code, and any terminal that predates the installation before
testing command-name resolution.

The existing OpenCode configuration and the `agent-engineer` /
`special-builder` Claude subagents invoke `codebase-memory-mcp` directly, so they
need no client registration. Claude Code's **main session** does need one, and
`claude_mcp_servers.ps1` performs it on apply (see the table below) because the
`cbm` entry in `configs/claude-mcp.json` lists `windows` in `platforms`. macOS,
WSL2, and the Dev Container use the same config; see
`docs/automation/claude-mcp.md`.

The hook skips with an `INFO:` line when `claude` is not on `PATH` during the
apply. Register it by hand in that case:

```powershell
claude mcp add --transport stdio --scope user cbm -- codebase-memory-mcp
```

Unless `CBM_CACHE_DIR` is set at runtime, the standard v0.9.0 binary keeps its
unmanaged databases, configuration, and logs under
`~/.cache/codebase-memory-mcp`. The install hook creates the active cache
directory so read-only commands work before the first index, but chezmoi does
not manage its contents. Verify the native installation from a fresh
PowerShell/OpenCode process:

```powershell
codebase-memory-mcp --version
codebase-memory-mcp cli list_projects
opencode mcp list
```

The version must match `.chezmoidata.yaml`, `list_projects` must complete
successfully, and the `cbm` entry must report connected. Provisioning does not
index the current repository automatically.

## AI Automation Hook Audit

The Windows-capable AI automation hooks have these explicit apply decisions:

| Hook Target | Decision | Rationale and Guardrails |
| :--- | :--- | :--- |
| `claude_mcp_servers.ps1` | Admitted | The hook accepts only user scope, stores no tokens, filters platforms and transports, skips missing executable candidates, and leaves existing registrations unchanged unless `replace` is enabled. Set `CLAUDE_MCP_DRY_RUN=1` to preview actions. |
| `install_plannotator.ps1` | Admitted | The binary-only hook selects the native x64 or arm64 release, verifies its pinned-version sidecar checksum and candidate version, then publishes through staged replacement with rollback. It does not run the upstream installer or mutate agent/plugin state. |
| `host_ai_plugin_refresh.ps1` | Excluded | The hook requires closed OpenCode sessions for some cache repairs and can recursively remove the OpenCode package cache. It remains ignored until cache-root containment, deletion scope, and apply-from-OpenCode behavior receive a separate native-Windows redesign and test. |

Use `PLANNOTATOR_INSTALL_DRY_RUN=1` to report the selected release asset and
destination without downloading or publishing the Plannotator binary. See
`docs/plannotator.md` for native installation and retry details.

## Claude Code `jq` Runtime Dependency

Native Windows uses the `jqlang.jq` WinGet package for the `jq` executable used
by Claude Code. `configs/winget-packages.json` records that package in the
exported Windows inventory, but the current chezmoi flow does not import the
manifest or install its packages automatically. On a clean host, install and
verify `jq` from PowerShell:

```powershell
winget install --id jqlang.jq --exact --source winget
Get-Command jq
jq --version
```

When `jq` is absent, the Claude statusline prints `Claude [needs jq]` and exits
successfully without its detailed status. The Beads destructive-action hook
also exits without a decision, leaving Claude's normal permission system and
the subagent prompt contracts in effect. This fail-open behavior prevents a
missing parser from blocking unrelated commands, but removes that hook's
defense-in-depth guardrail until `jq` is available.

## Current Exclusions

The Windows section of `.chezmoiignore` uses a minimal whitelist:

- Most Linux and macOS targets remain ignored.
- `Documents/PowerShell/**` remains ignored as a direct target because the sync
  hook handles it.
- Linux/macOS configuration trees such as `.config/mise`, `.config/lazygit`,
  and `.config/sublime-merge` remain ignored.
- `host_ai_plugin_refresh.ps1` remains excluded for the cache-safety and active
  OpenCode-session constraints documented above.

## Verify On This Machine

```powershell
chezmoi managed
chezmoi ignored
chezmoi diff --verbose
```

Direct targets and admitted hooks should appear under `managed`; excluded
targets and hooks should appear under `ignored`. Verify sync destinations from
the hook source and destination filesystem because they are not direct managed
targets.
