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

# Inventory: Windows (PowerShell)

This page lists the main managed targets expected on native Windows.

## Core Dotfiles

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| Git | `~/.gitconfig`, `~/.gitignore_global` | `dot_gitconfig.tmpl`, `dot_gitignore_global.tmpl` |
| Markdownlint | `~/.markdownlint-cli2.jsonc` | `dot_markdownlint-cli2.jsonc` |
| Claude | `~/.claude/**` | `dot_claude/**` |

## Windows-Specific Config

| Area | Target Path | Source Pattern |
| :--- | :--- | :--- |
| PowerShell profile | `~/Documents/PowerShell/Microsoft.PowerShell_profile.ps1` | `private_Documents/PowerShell/Microsoft.PowerShell_profile.ps1` |
| PowerShell scripts | `~/Documents/PowerShell/Scripts/**` | `private_Documents/PowerShell/Scripts/**` |
| PowerShell modules | `~/.config/powershell/*.ps1` | `private_dot_config/powershell/*.ps1.tmpl` |
| Espanso | `~/AppData/Roaming/espanso/match/**` | `AppData/Roaming/espanso/match/**` |

## Notes About Scope

On Windows, `.chezmoiignore` uses a minimal whitelist strategy. This is intentional:

- Most non-Windows targets are ignored.
- Only selected files under `.chezmoiscripts/` are unignored.
- `Documents/PowerShell/**` and `.config/powershell/**` are the primary shell automation surface.

## Verify On This Machine

```powershell
chezmoi managed
chezmoi diff --verbose
```
