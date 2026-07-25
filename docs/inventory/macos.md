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
| Doom Emacs | `~/.config/doom/**` | `private_dot_config/doom/**` |
| LazyGit | `~/Library/Application Support/lazygit/config.yml` | `private_Library/private_Application Support/lazygit/config.yml` |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/**` | `private_Library/private_Application Support/Sublime Merge/Packages/**` |
| Espanso | `~/Library/Application Support/espanso/match/**` | `private_Library/private_Application Support/espanso/match/**` |

## macOS Services and Scripts

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| LaunchAgents | `~/Library/LaunchAgents/*.plist` | `private_Library/LaunchAgents/*.plist.tmpl` |
| User bin scripts | `~/bin/vnc_monitor.sh`, `~/bin/nfs_dot_clean.sh` | `bin/executable_vnc_monitor.sh`, `bin/executable_nfs_dot_clean.sh` |
| Devcontainer launcher | `~/bin/devcontainer-launch` | `bin/executable_devcontainer-launch.tmpl` |
| Browser policy profiles | `~/.local/share/dotfiles/browser-policies/justthebrowser/*.mobileconfig` | `.chezmoiscripts/run_onchange_after_browser-policies.sh.tmpl`, `configs/browser-policies/**` |

## NFS AppleDouble Cleanup

The `com.user.nfs-dot-clean` LaunchAgent runs `~/bin/nfs_dot_clean.sh` every
5 minutes and on mount events. Configure local cleanup roots in
`~/.config/nfs-dot-clean/paths`, one path per line. Blank lines and `#`
comments are ignored.

Enable or update the job on a macOS host:

```sh
chezmoi apply
mkdir -p ~/.config/nfs-dot-clean
$EDITOR ~/.config/nfs-dot-clean/paths
if ! launchctl print gui/$(id -u)/com.user.nfs-dot-clean >/dev/null 2>&1; then
  launchctl bootstrap gui/$(id -u) \
    ~/Library/LaunchAgents/com.user.nfs-dot-clean.plist
fi
launchctl kickstart -k gui/$(id -u)/com.user.nfs-dot-clean
```

If the LaunchAgent log reports `Operation not permitted`, macOS privacy
controls are probably blocking the launchd-started shell from accessing the
NFS volume. Grant Full Disk Access to `/bin/bash` and `/usr/sbin/dot_clean` in
System Settings, then run the `launchctl kickstart` command again.

## Container Build Sync (macOS)

On macOS, container build files are synced to:

- `~/Documents/development/container-dotfiles/**`

See `docs/devcontainers.md` for build/runtime details.

## Verify On This Machine

```sh
chezmoi managed
chezmoi diff --verbose
```
