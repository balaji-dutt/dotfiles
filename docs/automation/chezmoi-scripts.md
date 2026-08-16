<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Chezmoi Script Hooks

Chezmoi executes scripts in `.chezmoiscripts/` based on filename conventions.

## Trigger Types

- `run_once_*`: executes once on a machine.
- `run_after_*`: executes after apply.
- `run_onchange_*`: executes when the script source changes.

## Current Script Catalog

| Script | Trigger | Typical Scope |
| :--- | :--- | :--- |
| `run_onchange_before_00-wsl-provision.sh.tmpl` | onchange | WSL Ansible provisioning inputs |
| `run_once_before_copy_ansible_key.sh.tmpl` | once | ansible key placement |
| `run_once_before_copy_sublime_merge_packages.sh.tmpl` | once | Sublime Merge package sync |
| `run_once_before_copy_sublime_merge_packages.ps1.tmpl` | once | Windows Sublime Merge sync |
| `run_once_after_97-retire-macos-beads-dolt-links.sh.tmpl` | once | retire legacy macOS mise shim links |
| `run_once_after_99-cleanup-wrong-apply.sh.tmpl` | once | cleanup after wrong apply |
| `run_once_after_99-cleanup-wrong-apply.ps1.tmpl` | once | Windows cleanup variant |
| `run_once_after_98-migrate-opencode-quota.ps1.tmpl` | once | migrate the Windows quota sidecar to APPDATA |
| `run_after_10-dotfiles-commit-template.sh.tmpl` | after | commit template and repo hook path helper |
| `run_after_10-dotfiles-commit-template.ps1.tmpl` | after | Windows commit template and repo hook path helper |
| `run_after_configure_git_templates.sh.tmpl` | after | git template wiring |
| `run_after_macos-nfs-config.sh.tmpl` | after | macOS system NFSv4 client default reconciliation |
| `run_after_update_copyq.sh.tmpl` | after | CopyQ refresh |
| `run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl` | after | Debian WSL2 devcontainer overlays |
| `run_after_windows-beads-client.ps1.tmpl` | after | Windows Beads client environment wiring |
| `run_after_windows-beads-pin.ps1.tmpl` | after | Windows Beads Winget pin reconciliation |
| `run_after_windows-sync.ps1.tmpl` | after | Windows sync flow |
| `run_after_windows-zz-register-startup-tasks.ps1.tmpl` | after | Windows startup task registration |
| `run_onchange_after_claude_mcp_servers.sh.tmpl` | onchange | Claude MCP server registration |
| `run_onchange_after_claude_mcp_servers.ps1.tmpl` | onchange | Windows Claude MCP server registration |
| `run_onchange_after_host_ai_plugin_refresh.sh.tmpl` | onchange | Claude/OpenCode host plugin refresh |
| `run_onchange_after_host_ai_plugin_refresh.ps1.tmpl` | onchange | Windows host plugin refresh source (not admitted) |
| `run_onchange_after_install_plannotator.sh.tmpl` | onchange | Host Plannotator CLI installation |
| `run_onchange_after_install_plannotator.ps1.tmpl` | onchange | Windows Plannotator CLI installation |
| `run_onchange_after_install_packages.sh.tmpl` | onchange | non-WSL package installs |
| `run_onchange_after_macos-vdi-apps.sh.tmpl` | onchange | macOS Citrix/Zoom VDI version reporting and opt-in installs |
| `run_onchange_after_reload_launch_agents.sh.tmpl` | onchange | LaunchAgent reload, including NFS dot-clean |
| `run_onchange_after_ansible_syntax_image.sh.tmpl` | onchange | Ansible syntax image update |
| `run_onchange_after_windows-bootstrap.ps1.tmpl` | onchange | Windows bootstrap flow |
| `run_copy_win_gitignore.sh.tmpl` | helper | windows gitignore helper |

## Platform Guidance

- Keep scripts templated and OS-gated.
- Use the canonical `.chezmoi.toml.tmpl` data flags (`isWSL`, `isWSL2`,
  `isDebianWSL2`, `isUbuntuWSL2`, `isDevcontainerHost`) in active templates;
  keep local fallbacks only where scripts may render before config regeneration.
- Follow `docs/agents/ADDING_SCRIPTS.md` when adding new scripts.
- User-scope Claude MCP server registration is configured by
  `configs/claude-mcp.json`; see `docs/automation/claude-mcp.md`.
- Native Windows admits the Claude MCP and Plannotator install hooks, but keeps
  `host_ai_plugin_refresh.ps1` excluded; see `docs/inventory/windows.md`.
- The host plugin refresh hook reads `configs/host-ai-plugin-refresh.jsonc` and
  `dot_claude/settings-base.json`, but only the former is hashed into its
  onchange trigger; see `docs/devcontainers.md`.
- A failed Claude plugin refresh reports an `ERROR:` and continues to the
  remaining plugins and the OpenCode cache step; a failed marketplace update is
  likewise non-fatal. The hook exits non-zero at the end when either failed.
- Citrix Workspace and Zoom VDI are handled outside Homebrew; see
  `docs/automation/macos-vdi-apps.md`.
- Validate changed scripts with `./assets/cz-audit.sh check <repo-relative-path>`.
