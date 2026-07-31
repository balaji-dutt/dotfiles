<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Legacy Program Dotfiles Grid

This file preserves the previous all-in-one matrix from `README.md`.

Status: deprecated snapshot kept for historical reference.

Use curated inventories for current documentation:

- `docs/inventory/macos.md`
- `docs/inventory/wsl2.md`
- `docs/inventory/windows.md`

## Legacy Grid (Snapshot)

| Program Name | Rendered Dotfile Path ($HOME relative) | Is Template? | macOS | Linux | Generic WSL2 | Ubuntu (WSL2) | Debian (WSL2) | Has Scripts? |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Bash | `~/.bashrc` | Yes | | | ✅ | ✅ | | No |
| Claude | `~/.claude/settings.json` | No | ✅ | ✅ | ✅ | | | No |
| Claude | `~/.claude/AGENTS.md` | No | ✅ | ✅ | ✅ | | | No |
| Claude | `~/.claude/CLAUDE.md` | No | ✅ | ✅ | ✅ | | | No |
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
| Markdownlint | `~/.markdownlint-cli2.jsonc` | No | ✅ | ✅ | ✅ | | | No |
| Mise | `~/.config/mise/config.toml` | Yes | ✅ | ✅ | ✅ | | | No |
| Powerlevel10k | `~/.p10k.zsh` | Yes | ✅ | ✅ | ✅ | | | No |
| Powerlevel10k | `~/.local/config/.p10k.zsh` | No | ✅ | ✅ | ✅ | | | No |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/User/Default.sublime-commands` | No | ✅ | | | | | Yes |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/User/Commit Message.sublime-settings` | No | ✅ | | | | | Yes |
| Sublime Merge | `~/Library/Application Support/Sublime Merge/Packages/User/Preferences.sublime-settings` | No | ✅ | | | | | Yes |
| Sublime Merge | `~/.config/sublime-merge/Packages/User/Default.sublime-commands` | No | | ✅ | ✅ | | | Yes |
| Sublime Merge | `~/.config/sublime-merge/Packages/User/Commit Message.sublime-settings` | No | | ✅ | ✅ | | | Yes |
| Sublime Merge | `~/.config/sublime-merge/Packages/User/Preferences.sublime-settings` | Yes | | | ✅ | | | Yes |
| Zsh | `~/.zshrc` | Yes | ✅ | ✅ | ✅ | | | No |
| Zsh | `~/.zsh_plugins.txt` | Yes | ✅ | ✅ | ✅ | | | No |
| Container Build Files | `Documents/development/container-dotfiles/**` | Yes | ✅ | | | ❌ | ✅ | No |
