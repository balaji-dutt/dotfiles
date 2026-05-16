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
| `run_once_before_00_install_custom_fonts.sh.tmpl` | once | font installation |
| `run_once_before_copy_ansible_key.sh.tmpl` | once | ansible key placement |
| `run_once_before_copy_sublime_merge_packages.sh.tmpl` | once | Sublime Merge package sync |
| `run_once_before_copy_sublime_merge_packages.ps1.tmpl` | once | Windows Sublime Merge sync |
| `run_once_after_emacs_spellcheck.sh.tmpl` | once | Emacs spellcheck setup |
| `run_once_after_99-cleanup-wrong-apply.sh.tmpl` | once | cleanup after wrong apply |
| `run_once_after_99-cleanup-wrong-apply.ps1.tmpl` | once | Windows cleanup variant |
| `run_after_10-dotfiles-commit-template.sh.tmpl` | after | commit template helper |
| `run_after_10-dotfiles-commit-template.ps1.tmpl` | after | Windows commit template helper |
| `run_after_configure_git_templates.sh.tmpl` | after | git template wiring |
| `run_after_update_copyq.sh.tmpl` | after | CopyQ refresh |
| `run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl` | after | Debian WSL2 devcontainer overlays |
| `run_after_windows-sync.ps1.tmpl` | after | Windows sync flow |
| `run_after_windows-zz-register-startup-tasks.ps1.tmpl` | after | Windows startup task registration |
| `run_onchange_after_claude_mcp_servers.sh.tmpl` | onchange | Claude MCP server registration |
| `run_onchange_after_install_packages.sh.tmpl` | onchange | non-WSL package installs |
| `run_onchange_after_reload_launch_agents.sh.tmpl` | onchange | LaunchAgent reload, including NFS dot-clean |
| `run_onchange_after_ansible_syntax_image.sh.tmpl` | onchange | Ansible syntax image update |
| `run_onchange_after_windows-bootstrap.ps1.tmpl` | onchange | Windows bootstrap flow |
| `run_copy_win_gitignore.sh.tmpl` | helper | windows gitignore helper |

## Platform Guidance

- Keep scripts templated and OS-gated.
- Follow `docs/agents/ADDING_SCRIPTS.md` when adding new scripts.
- User-scope Claude MCP server registration is configured by
  `configs/claude-mcp.json`; see `docs/automation/claude-mcp.md`.
- Validate changed scripts with `./assets/cz-audit.sh check <repo-relative-path>`.
