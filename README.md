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

## Dotfiles README

This repo contains configuration files that I use across Linux & macOS, managed using [chezmoi](https://www.chezmoi.io/).

The list of configurations that I'm currenly managing through this repo are:

* Bash Shell
* Git
* Ansible
* Sublime Merge
* lazygit
* Emacs (more specifically the Doom Emacs framework) configuration and dependent shell scripts covering:
  * Custom fonts
  * Emacs tabs (centaur-tabs)
  * Emacs spellcheck (hunspell)
  * org-mode
  * Custom elisp functions.
* ZSH Shell with the following frameworks:
  * antidote (with zprezto and oh-my-zsh framework functions)
  * powerlevel10k
* Brew package manager
* Mise (Runtime Manager)
* Claude Code (Settings)
* Claude Code Router (Config)
* Markdownlint
* Package Managers (tracked lists):
  * uv tools
  * npm global packages
  * bun global packages
  * bunx commands (audit log)
* Devcontainers

## Program Dotfiles

| Program Name | Rendered Dotfile Path ($HOME relative) | Is Template? | macOS | Linux | Generic WSL2 | Ubuntu (WSL2) | Debian (WSL2) | Has Scripts? |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Bash | `~/.bashrc` | Yes | | | ✅ | ✅ | | No |
| Claude | `~/.claude/settings.json` | No | ✅ | ✅ | ✅ | | | No |
| Claude | `~/.claude/AGENTS.md` | No | ✅ | ✅ | ✅ | | | No |
| Claude | `~/.claude/CLAUDE.md` | No | ✅ | ✅ | ✅ | | | No |
| Claude Code Router | `~/.claude-code-router/config.json` | Yes | ✅ | ✅ | ✅ | | | No |
| Claude Code Router | `~/.claude-code-router/plugins/` | No | ✅ | ✅ | ✅ | | | No |
| Emacs | `~/.config/doom/config.el` | Yes | ✅ | | | ✅ | | Yes |
| Emacs | `~/.config/doom/custom.el` | No | ✅ | | | ✅ | | Yes |
| Emacs | `~/.config/doom/init.el` | No | ✅ | | | ✅ | | Yes |
| Emacs | `~/.config/doom/packages.el` | No | ✅ | | | ✅ | | Yes |
| Git | `~/.gitconfig` | Yes | ✅ | ✅ | ✅ | | | Yes |
| Git | `~/.gitignore_global` | Yes | ✅ | | | | | Yes |
| LazyGit | `~/Library/Application Support/lazygit/config.yml` | No | ✅ | | | | | No |
| LazyGit | `~/.config/lazygit/config.yml` | No | | ✅ | ✅ | | | No |
| macOS | `~/Library/LaunchAgents/Environment.plist` | Yes | ✅ | | | | | No |
| macOS | `~/Library/LaunchAgents/com.ssh-add-keychain.plist` | No | ✅ | | | | | No |
| macOS | `~/Library/LaunchAgents/com.user.vncmonitor.plist` | Yes | ✅ | | | | | No |
| macOS | `~/bin/vnc_monitor.sh` | No | ✅ | | | | | No |
| macOS | `~/Library/LaunchAgents/com.user.ccr.plist` | Yes | ✅ | | | | | No |
| macOS | `~/bin/ccr_launcher.sh` | Yes | ✅ | | | | | No |
| Markdownlint | `~/.markdownlint-cli2.jsonc` | No | ✅ | ✅ | ✅ | | | No |
| Mise | `~/.config/mise/config.toml` | Yes | ✅ | ✅ | ✅ | | | No |
| Powerlevel10k | `~/.p10k.zsh` | Yes | ✅ | ✅ | ✅ | | | No |
| Powerlevel10k | `~/.local/config/.p10k.zsh` | No | ✅ | ✅ | ✅ | | | No |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/custom.sublime-commands` | No | ✅ | | | | | Yes |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/User/Commit Message.sublime-settings` | No | ✅ | | | | | Yes |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/User/Preferences.sublime-settings` | No | ✅ | | | | | Yes |
| Sublime Merge | `~/.config/sublime-merge/Packages/custom.sublime-commands` | No | | ✅ | ✅ | | | Yes |
| Sublime Merge | `~/.config/sublime-merge/Packages/User/Commit Message.sublime-settings` | No | | ✅ | ✅ | | | Yes |
| Sublime Merge | `~/.config/sublime-merge/Packages/User/Preferences.sublime-settings` | Yes | | | ✅ | | | Yes |
| Zsh | `~/.zshrc` | Yes | ✅ | ✅ | ✅ | | | No |
| Zsh | `~/.zsh_plugins.txt` | Yes | ✅ | ✅ | ✅ | | | No |
| **Container Build Files** | `Documents/development/container-dotfiles/**` | Yes | ✅ | | | ❌ | ✅ | No |

### Container Build Configurations

These files configure the **development container build process** and are **synced to your host** before container creation. They define what gets installed IN the container.

**Platform Sync Status:**
* ✅ **macOS**: Container files synced to `~/Documents/development/container-dotfiles/`
* ✅ **Debian WSL2**: Container files synced to `~/Documents/development/container-dotfiles/`
* ❌ **Ubuntu WSL2**: Container files NOT synced (see `.chezmoiignore`)
* ❌ **Generic Linux**: Container files NOT synced

| Config Type | File Path (Source Repo → Dest Host) | Description | Used During |
| :--- | :--- | :--- | :--- |
| Devcontainer Config | `private_Documents/.../devcontainer.json.tmpl` → `Documents/.../devcontainer.json.tmpl` | VS Code devcontainer definition | Container build only |
| Package Lists | `private_Documents/.../configs/*.txt` → `Documents/.../configs/*.txt` | Software packages for container | Container build only |
| Install Script | `private_Documents/.../dotfiles/install.sh.tmpl` → `Documents/.../dotfiles/install.sh` | Container initialization script | Container build only |

### Container Runtime Files

These files are **generated by the render script** and contain secrets. They live ONLY in the container, never commiittted to git.

| Config Type | File Path (In Container) | Description | Created By |
| :--- | :--- | :--- | :--- |
| Claude Code Private Settings | `~/.claude/private_settings.json` | Claude Code permissions | Copied from host dotfiles |
| Claude Code Router Config | `~/.claude-code-router/config.json` | Router API keys, providers, and port configuration (`ccr_port` variable) | `./assets/render-container-configs.sh` |
| Container Environment | `~/.bashrc`, `~/.zshrc` | TAVILY_API_KEY export | `./assets/render-container-configs.sh` via install.sh |

**Important:**
* Run `./assets/render-container-configs.sh` **before rebuilding container** to inject API keys
* Container dotfiles configure the *container environment*, not your host system
* The files are read during `postCreateCommand` to install tools **INSIDE** the container

## Scripts

| Program Name | Script Name | macOS | Linux | Generic WSL2 | Ubuntu (WSL2) | Debian (WSL2) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| Emacs | `run_once_after_emacs_spellcheck.sh.tmpl` | ✅ | | | ✅ | |
| Package Management | `run_onchange_after_install_packages.sh.tmpl` | ✅ | ✅ | | ✅ | ✅ |
| Sublime Merge | `run_once_before_copy_sublime_merge_packages.sh.tmpl` | ✅ | | ✅ | | |
| System | `run_once_before_00_install_custom_fonts.sh.tmpl` | ✅ | | | ✅ | |
| System | `run_once_before_00-wsl-provision.sh.tmpl` | | | ✅ | ✅ | ✅ |
| System | `run_onchange_after_reload_launch_agents.sh.tmpl` | ✅ | | | | |

## Bootstrap Process

### For WSL2 Instances

1. **Initial Bootstrap**: Run `./bootstrap-wsl.sh` to install:
   * Ansible (via pipx; bootstrap only)
   * Ansible collections
   * lastversion
   * chezmoi
   * Initialize chezmoi with local repository

2. **Full Provisioning**: Run `chezmoi apply` to trigger:
   * Script: `run_once_before_00-wsl-provision.sh.tmpl`
   * Ansible playbook: `ansible/wsl-playbook.yml`
   * Package installation via `run_onchange_after_install_packages.sh.tmpl`

   #### Ansible Playbook Components

   The following Ansible components are executed as part of the provisioning process:

   **Main Playbooks**
   * `ansible/wsl-playbook.yml` - Main playbook that orchestrates all WSL2 provisioning tasks
   * `ansible/requirements.yml` - Ansible collection dependencies (community.general, ansible.posix)

   **Task Files**
   * `ansible/tasks/apt-repos.yml` - Configures APT repositories for all WSL2 instances
   * `ansible/tasks/base-packages.yml` - Installs base packages (fzf, ripgrep, curl, etc.)
   * `ansible/tasks/zsh-setup.yml` - Installs and configures Zsh with Antidote plugin manager
   * `ansible/tasks/system-config.yml` - Applies system-level configurations
   * `ansible/tasks/ubuntu-extras.yml` - Ubuntu-specific packages and tools (gedit, nautilus, wslu)
   * `ansible/tasks/emacs.yml` - Installs and configures Emacs (Ubuntu only)
   * `ansible/tasks/debian-dev-tools.yml` - Debian-specific development tools
   * `ansible/tasks/certificates.yml` - Installs SSL certificates (conditional, if cert_path provided)
   * `ansible/tasks/onepassword-setup.yml` - Configures 1Password CLI (conditional, if op_account provided)

## Configuration Files

| Filename | Purpose | Structure |
| :--- | :--- | :--- |
| `configs/packages.yaml` | Single source of truth for WSL2 apt packages | YAML with apt packages + external tool versions |
| `configs/mise.toml` | Mise tool versions and plugins | TOML format |
| `configs/uv_tools.txt` | uv-installed tools for Python CLIs | Plain text list |
| `configs/npm_globals.txt` | npm global packages | Plain text list (Auto-generated) |
| `configs/npm_globals_linux.txt` | npm global packages (WSL2) | Plain text list |
| `configs/bun_globals.txt` | bun global packages | Plain text list (Auto-generated) |
| `brewfile.txt` | Homebrew Bundle file | List of taps, brews, casks, and mas apps |
| `bootstrap-wsl.sh` | Bootstrap script for new WSL2 instances | Bash script |

### Package Categories in packages.yaml

* `base_apt_packages` - Core packages for all WSL2 instances (build-essential, curl, git, etc.)
* `ubuntu_apt_packages` - Ubuntu-specific packages (gedit, nautilus, wslu, etc.)
* Python CLI tools - installed via `uv tool` from `configs/uv_tools.txt`
* `versions` - GitHub release versions for external tools (lazygit, lazydocker)

## Auto-Commit / Self-Replicating Features

The ZSH configuration (`.zshrc`) includes custom wrapper functions for several package managers. These wrappers automatically commit changes to the dotfiles repository when packages are installed, removed, or updated. Additionally, the `~/.claude/commit-docs.sh` script provides automated commit functionality for `TODO.md` and `README.md` documentation files.

### Package Manager Wrappers

| Tool | Wrapper Function | Manifest File | Logic |
| :--- | :--- | :--- | :--- |
| **Homebrew** | `brew` | `brewfile.txt` | Dumps `Brewfile` on install/uninstall/tap/untap success and commits changes. |
| **Mise** | `mise` | `configs/mise.toml` | Commits changes to global config or local `mise.toml` if in a git repo. |
| **uv** | `uv` | `configs/uv_tools.txt` | Updates tool list and commits on `uv tool` operations. |
| **npm** | `npm` | `configs/npm_globals.txt` | Updates global package list and commits on global install/remove. |
| **Bun (globals)** | `bun` | `configs/bun_globals.txt` | Updates bun global package list and commits on `bun add -g` / `bun remove -g`. |
| **Bunx (audit)** | `bunx` | `.claude/bunx_commands.txt` or `.bunx/` | Logs executed commands and commits artifacts/logs if in a git repo. |

### Documentation Automation

| Tool | Script | Managed Files | Logic |
| :--- | :--- | :--- | :--- |
| **Claude Code** | `~/.claude/commit-docs.sh` | `TODO.md`, `README.md` | Handles formatted commits with automatic prefixing, timestamp generation, and proper message formatting for documentation updates. Supports TODO operations (add, complete, pause, resume) and README updates. |

## Variables

### Chezmoi Template Variables (`.chezmoi.toml.tmpl`)

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `name` | User's full name | Git configuration |
| `email` | User's email address | Git configuration |
| `CERTPATH` | Path to SSL certificate directory | Certificate installation scripts |
| `CERTFILES` | Space-separated certificate filenames | Certificate installation scripts |
| `ansible_key` | Path to Ansible SSH private key | (Reserved for future use) |
| `org_dir` | Path to Emacs org-mode directory | Emacs configuration |

#### WSL2-Specific Variables

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `homelab.nfs_server` | NFS server IP address | NFS mounting tasks |
| `homelab.nfs_path` | NFS export path | NFS mounting tasks |
| `homelab.windows_user` | Windows username | WSL integration |
| `onepassword.url` | 1Password account URL | 1Password CLI setup |
| `onepassword.email` | 1Password email address | 1Password CLI setup |
| `ccr_port` | Claude Code Router port number | Claude Code Router configuration, devcontainer PostStartCommand |
