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

# Inventory: macOS

This page lists the main managed targets expected on macOS.

## Core Dotfiles

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| Bash | `~/.bashrc` | `dot_bashrc.tmpl` |
| Zsh | `~/.zshrc`, `~/.zsh_plugins.txt` | `dot_zshrc.tmpl`, `dot_zsh_plugins.txt.tmpl` |
| Git | `~/.gitconfig` | `dot_gitconfig.tmpl` |
| Markdownlint | `~/.markdownlint-cli2.jsonc` | `dot_markdownlint-cli2.jsonc` |
| Prompt | `~/.p10k.zsh`, `~/.local/config/.p10k.zsh` | `symlink_dot_p10k.zsh.tmpl`, `dot_local/config/dot_p10k.zsh` |
| Mise | `~/.config/mise/config.toml` | `private_dot_config/mise/symlink_config.toml.tmpl` |
| Starship | `~/.config/starship.toml` | `private_dot_config/starship.toml` |

## Tool-Specific Config

| Tool | Target Path | Source Pattern |
| :--- | :--- | :--- |
| Claude | `~/.claude/**` | `dot_claude/**` |
| Claude Code Router | `~/.claude-code-router/**` | `dot_claude-code-router/**` |
| OpenCode | `~/.config/opencode/**` | `private_dot_config/opencode/**` |
| Doom Emacs | `~/.config/doom/**` | `private_dot_config/doom/**` |
| LazyGit | `~/Library/Application Support/lazygit/config.yml` | `private_Library/private_Application Support/lazygit/config.yml` |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/**` | `private_Library/private_Application Support/Sublime Merge/Packages/**` |
| Espanso | `~/Library/Application Support/espanso/match/**` | `private_Library/private_Application Support/espanso/match/**` |

## macOS Services and Scripts

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| LaunchAgents | `~/Library/LaunchAgents/*.plist` | `private_Library/LaunchAgents/*.plist.tmpl` |
| User bin scripts | `~/bin/ccr_launcher.sh`, `~/bin/vnc_monitor.sh` | `bin/executable_ccr_launcher.sh.tmpl`, `bin/executable_vnc_monitor.sh` |

## Container Build Sync (macOS)

On macOS, container build files are synced to:

- `~/Documents/development/container-dotfiles/**`

See `docs/devcontainers.md` for build/runtime details.

## Verify On This Machine

```sh
chezmoi managed
chezmoi diff --verbose
```
