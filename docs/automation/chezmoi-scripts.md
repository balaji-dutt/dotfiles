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
| `run_once_after_97-retire-codebase-memory-mcp-ubi.sh.tmpl` | once | retire legacy CBM UBI installs on macOS and WSL2 |
| `run_once_after_99-cleanup-wrong-apply.sh.tmpl` | once | cleanup after wrong apply |
| `run_once_after_99-cleanup-wrong-apply.ps1.tmpl` | once | Windows cleanup variant |
| `run_once_after_98-migrate-opencode-quota.ps1.tmpl` | once | migrate the Windows quota sidecar to APPDATA |
| `run_after_10-dotfiles-commit-template.sh.tmpl` | after | dotfiles repo commit template helper |
| `run_after_10-dotfiles-commit-template.ps1.tmpl` | after | Windows dotfiles repo commit template helper |
| `run_after_20-git-template-hooks.sh.tmpl` | after | existing repository Git hook reconciliation |
| `run_after_20-git-template-hooks.ps1.tmpl` | after | Windows existing repository Git hook reconciliation |
| `run_after_configure_git_templates.sh.tmpl` | after | git template wiring |
| `run_after_macos-nfs-config.sh.tmpl` | after | macOS system NFSv4 client default reconciliation |
| `run_after_macos-opencode-pin.sh.tmpl` | after | hold an installed stable OpenCode v1 with Homebrew |
| `run_after_update_copyq.sh.tmpl` | after | CopyQ refresh |
| `run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl` | after | Debian WSL2 devcontainer overlays |
| `run_after_windows-beads-client.ps1.tmpl` | after | Windows Beads client environment wiring |
| `run_after_windows-beads-pin.ps1.tmpl` | after | Windows Beads Winget pin reconciliation |
| `run_after_windows-opencode-pin.ps1.tmpl` | after | hold an installed stable OpenCode v1 with Chocolatey |
| `run_after_windows-sync.ps1.tmpl` | after | Windows sync flow |
| `run_after_windows-zz-register-startup-tasks.ps1.tmpl` | after | Windows startup task registration |
| `run_after_zz-configure-codebase-memory-mcp.sh.tmpl` | after | POSIX/WSL cache-local CBM auto-index configuration |
| `run_after_zz-configure-codebase-memory-mcp.ps1.tmpl` | after | Windows cache-local CBM auto-index configuration |
| `run_onchange_after_claude_mcp_servers.sh.tmpl` | onchange | Claude MCP server registration |
| `run_onchange_after_claude_mcp_servers.ps1.tmpl` | onchange | Windows Claude MCP server registration |
| `run_onchange_after_host_ai_plugin_refresh.sh.tmpl` | onchange | Claude/OpenCode host plugin refresh |
| `run_onchange_after_host_ai_plugin_refresh.ps1.tmpl` | onchange | Windows Claude/OpenCode host plugin refresh |
| `run_onchange_after_install_plannotator.sh.tmpl` | onchange | Host Plannotator CLI installation |
| `run_onchange_after_install_plannotator.ps1.tmpl` | onchange | Windows Plannotator CLI installation |
| `run_onchange_after_install_packages.sh.tmpl` | onchange | non-WSL package installs |
| `run_onchange_after_install_promptfoo_runtime.sh.tmpl` | onchange | macOS lockfile-backed Promptfoo runtime |
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
- Native Windows admits the Claude MCP, Plannotator install, and host plugin
  refresh hooks; see `docs/inventory/windows.md`.
- The two always-run CBM configuration hooks execute after platform installers,
  set `auto_index=true` only when needed, and verify the cache-local value. They
  never call CBM's native installer or export a repository graph. CBM refuses to
  start its CLI while another CBM process holds the daemon admission gate, so
  when one is running the hooks warn that `auto_index` went unverified and exit
  0 rather than failing the apply. With no CBM process running, a CLI failure is
  still fatal. The Windows hook additionally bounds each call at 150 seconds and
  classifies that timeout the same way; the POSIX hook stays unbounded because
  CBM ends its own startup wait within roughly 130 seconds.
- The CBM UBI retirement hook requires an inactive UBI backend and a healthy
  GitHub-backed CBM before uninstalling each fully qualified UBI version it
  discovers. It never prunes mise or deletes install directories directly.
- Citrix Workspace and Zoom VDI are handled outside Homebrew; see
  `docs/automation/macos-vdi-apps.md`.
- Validate changed scripts with `./assets/cz-audit.sh check <repo-relative-path>`.
- OpenCode holds freeze the installed host version, not a shared fleet version.
  Missing packages are not protected; see [OpenCode v1](opencode-v1.md) for
  installation, reviewed upgrades, pin verification, and retirement.

## Host AI Plugin Refresh

The POSIX and Windows host plugin refresh hooks use
`configs/host-ai-plugin-refresh.jsonc` as a Renovate trigger/sentinel, separate
from devcontainer package installation and lifecycle updates.

### Manifest and version ownership

- Host runtime configs may use `@latest`. OpenCode sentinel versions should
  come from the host package cache or npm latest, not devcontainer package pins.
- `@slkiser/opencode-quota` and `@tarquinen/opencode-dcp` each use a coordinated
  exact version across five references: host/container runtime configs,
  host/container TUI configs, and the refresh sentinel. Renovate groups each
  plugin's references so version changes and cache refreshes stay together.
- Update the manifest when host Claude/OpenCode plugin entries change. Only
  the manifest is hashed into the onchange trigger; edits to runtime configs or
  `settings-base.json` alone do not trigger a refresh.
- Claude plugin ids must match the `enabledPlugins` keys in
  `dot_claude/settings-base.json`. The hook reads that file at runtime and
  aborts before any `claude plugin` call if the ids disagree. A plugin rename
  therefore requires both files to be updated.
- Keep each manifest `version` and its `// renovate:` comment on one line so
  Renovate can match it.

### Refresh and retry behavior

- The hook installs manifest plugins without an install record and updates the
  rest; it does not require a prior Claude Code launch to install them.
- The host plugin hook derives required marketplace names from canonical
  `<plugin>@<marketplace>` ids, updates each required marketplace by name, and
  gives only Claude mutation children
  `CLAUDE_CODE_PLUGIN_KEEP_MARKETPLACE_ON_FAILURE=1`,
  `GIT_TERMINAL_PROMPT=0`, and a strict, noninteractive OpenSSH command. It then
  requires the CLI-reported marketplace catalog to exist and publish the
  configured plugin before update/install. Missing OpenSSH fails before launch;
  host-key failures point to fingerprint verification and OpenSSH `known_hosts`
  recovery instead of using PuTTY/Plink's separate cache.
- On Windows, each Claude mutation has a 120-second deadline and runs in its own
  kill-on-close Job Object. The hook reaps that command's descendants on normal
  return or timeout, and fails before launch if containment cannot be
  established. It never scans for or kills unrelated Git processes.
- A failed named marketplace update is non-fatal when its preserved catalog is
  still valid, but the hook exits nonzero at the end so chezmoi retries. An
  unavailable or malformed catalog skips only its own plugins; healthy
  marketplaces continue after non-timeout failures. The first marketplace or
  plugin timeout stops later Claude mutations, while the OpenCode cache step
  still runs and the hook exits nonzero for retry. A listed plugin whose update
  hits a stale install record falls back to installation, while other update
  failures do not trigger reinstall.
- On POSIX, the hook clears the OpenCode packages cache when no blocking
  OpenCode session is detected. A plugin can declare a staged npm cache install
  for a pinned compatibility workaround; while OpenCode is running, the hook
  may add a new versioned cache key but never replace an existing one. Detached
  or zombie OpenCode server processes are logged and ignored.
- On Windows, Claude refresh commands run before OpenCode process gating. An
  interactive, ambiguous, or uninspectable OpenCode process defers only cache
  mutation and exits nonzero so the onchange hook retries after OpenCode closes.
  An explicit `opencode serve` process identified through CIM is logged and
  ignored, matching the detached-server behavior on POSIX. The cache root is
  restricted to the normalized default or explicit XDG location, and recursive
  removal accepts only validated paths under its direct `packages` child.
  Process state is rechecked before removal and staged publication.
- A Windows deferral stops the current apply, so later hooks wait for the
  successful standalone-PowerShell retry; it is not a successful partial apply.
  Close blocking OpenCode clients and rerun `chezmoi apply` from standalone
  PowerShell.
- Use a directly rendered script with `HOST_AI_PLUGIN_REFRESH_DRY_RUN=1` for a
  state-preserving Windows preview. Do not use `chezmoi apply` only as a preview;
  see `docs/inventory/windows.md` for the command and standalone retry flow.
- Restart Claude Code/OpenCode after a refresh so the new plugin code is loaded.
