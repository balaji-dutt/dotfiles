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

# Inventory: WSL2 (Ubuntu/Debian)

This page lists the main managed targets expected on WSL2.

Canonical template predicates distinguish generic WSL (`isWSL`), WSL2
(`isWSL2`), Debian WSL2 (`isDebianWSL2`), Ubuntu WSL2 (`isUbuntuWSL2`), and
generic Linux. WSL1 is treated as WSL for prompt/config purposes, but WSL2-only
targets such as `~/bin/code` and Debian devcontainer helpers require the WSL2
predicates.

## Core Dotfiles

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| Bash | `~/.bashrc` | `dot_bashrc.tmpl` |
| Zsh | `~/.zshrc`, `~/.zsh_plugins.txt`, `~/.local/share/zsh-modern-cli-hints.zsh` | `dot_zshrc.tmpl`, `dot_zsh_plugins.txt.tmpl`, `dot_local/share/zsh-modern-cli-hints.zsh` |
| Git | `~/.gitconfig` | `dot_gitconfig.tmpl` |
| Markdownlint | `~/.markdownlint-cli2.jsonc` | `dot_markdownlint-cli2.jsonc` |
| Prompt | `~/.p10k.zsh`, `~/.local/config/.p10k.zsh` | `symlink_dot_p10k.zsh.tmpl`, `dot_local/config/dot_p10k.zsh` |
| Mise | `~/.config/mise/config.toml` | `private_dot_config/mise/symlink_config.toml.tmpl` |
| Starship | `~/.config/starship.toml` | `private_dot_config/starship.toml` |

## Tool-Specific Config

| Tool | Target Path | Source Pattern |
| :--- | :--- | :--- |
| Claude | `~/.claude/**` | `dot_claude/**` |
| OpenCode | `~/.config/opencode/**` | `private_dot_config/opencode/**` |
| Agent of Empires | `~/.config/agent-of-empires/config.toml` | `private_dot_config/agent-of-empires/modify_config.toml` |
| LazyGit | `~/.config/lazygit/config.yml` | `private_dot_config/lazygit/config.yml` |
| Sublime Merge | `~/.config/sublime-merge/Packages/**` | `private_dot_config/private_sublime-merge/private_Packages/**` |

## WSL2-Specific Targets

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| VS Code wrapper | `~/bin/code` | `bin/executable_code` |
| Devcontainer launcher (Debian only) | `~/bin/devcontainer-launch` | `bin/executable_devcontainer-launch.tmpl` |
| Devcontainer sync wrapper (Debian only) | `~/bin/sync-devcontainer-all.sh` | `bin/executable_sync-devcontainer-all.sh.tmpl` |
| Git ignore (WSL2 off) | `~/.gitignore_global` (not applied on WSL2 by ignore rules) | `dot_gitignore_global.tmpl` |

## Container Build Sync

`~/Documents/development/container-dotfiles/**` sync is WSL2 distro-dependent:

- Debian WSL2: synced
- Ubuntu WSL2: ignored by `.chezmoiignore`

The homelab VS Code workspace file under
`~/Documents/development/vscode-workspace/` follows the same Debian-only WSL2
policy and is removed from Ubuntu WSL2 during cleanup.

See `docs/devcontainers.md` for details.

## Verify On This Machine

```sh
chezmoi managed
chezmoi diff --verbose
```
