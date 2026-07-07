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

# Template Variables

Variables are defined in chezmoi templates/data files and consumed by dotfile templates and scripts.

## Common Variables

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `name` | user full name | git config |
| `email` | user email | git config |
| `CERTPATH` | certificate directory path | certificate install flows |
| `CERTFILES_RAW` | certificate filenames CSV | prompts + devcontainer hooks |
| `CERTFILES` | certificate filenames | certificate install flows |
| `ansible_key` | ansible ssh private key path | reserved/future use |

## WSL2-Focused Variables

Render-time platform predicates are emitted by `.chezmoi.toml.tmpl` and should
be preferred over ad hoc kernel checks in active templates:

| Variable | Meaning |
| :--- | :--- |
| `isWSL` | Linux under WSL1 or WSL2 (`osrelease` contains `microsoft`) |
| `isWSL2` | Linux under WSL2 (`isWSL` plus `osrelease` contains `wsl2`) |
| `isDebianWSL2` | Debian running under WSL2 |
| `isUbuntuWSL2` | Ubuntu running under WSL2 |
| `isDevcontainerHost` | macOS or Debian WSL2 host for container-dotfiles sync |

Templates that can render before chezmoi config regeneration keep local fallback
checks with `get . "isWSL" | default false` and the same kernel predicate.
Runtime shell/Python helpers use matching `is_wsl2` / `is_debian_wsl2` helpers
because they run outside chezmoi's template data model.

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `homelab.nfs_server` | NFS host | WSL mount tasks |
| `homelab.nfs_path` | NFS export path | WSL mount tasks |
| `homelab.windows_user` | Windows username | WSL integration |
| `onepassword.url` | 1Password account URL | 1Password setup |
| `onepassword.email` | 1Password account email | 1Password setup |
| `plannotator_port` | Plannotator direct-session fallback port | host shell env defaults |
| `plannotator_ports.host.build` | Host build-handoff Plannotator pool | `opencode-plannotator` |
| `plannotator_ports.host.claude` | Host Claude Code Plannotator pool | `claude-plannotator` |
| `plannotator_ports.host.custom` | Host stay-current custom Plannotator pool | `opencode-plannotator-custom` |
| `plannotator_ports.devcontainer.build` | Devcontainer build-handoff Plannotator pool | devcontainer env + wrapper defaults |
| `plannotator_ports.devcontainer.claude` | Devcontainer Claude Code Plannotator pool | devcontainer env + wrapper defaults |
| `plannotator_ports.devcontainer.custom` | Devcontainer stay-current custom Plannotator pool | devcontainer env + wrapper defaults |

## macOS Variables

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `macos_vdi.enabled` | Enables Citrix/Zoom VDI drift handling beyond passive reporting | macOS VDI hook |
| `macos_vdi.install` | Allows the VDI hook to install, not just report drift | macOS VDI hook |
| `macos_vdi.allow_downgrade` | Allows explicit VDI downgrades when true | macOS VDI hook |
| `macos_vdi.zoom.desired_pkg_version` | Committed desired full Zoom VDI `pkgutil` version | macOS VDI hook |
| `macos_vdi.zoom.pkg_url_op_ref` | Local 1Password ref for Zoom VDI `.pkg` URL | macOS VDI hook |
| `macos_vdi.zoom.pkg_sha256` | Optional Zoom VDI package checksum | macOS VDI hook |
| `macos_vdi.zoom.pkg_sha256_op_ref` | Optional local 1Password ref for Zoom checksum | macOS VDI hook |
| `macos_vdi.citrix.desired_family` | Committed desired Citrix Workspace version family | macOS VDI hook |
| `macos_vdi.citrix.display_version` | Human-readable committed Citrix desired version | macOS VDI hook |
| `macos_vdi.citrix.dmg_url_op_ref` | Local 1Password ref for Citrix `.dmg` URL | macOS VDI hook |
| `macos_vdi.citrix.dmg_path` | Local Citrix DMG fallback path | macOS VDI hook |
| `macos_vdi.citrix.default_dmg_path` | Public Citrix DMG fallback path | macOS VDI hook |
| `macos_vdi.citrix.dmg_sha256` | Optional Citrix DMG checksum | macOS VDI hook |
| `macos_vdi.citrix.dmg_sha256_op_ref` | Optional local 1Password ref for Citrix checksum | macOS VDI hook |

## Safety

- Keep secrets out of git.
- Prefer 1Password/runtime injection for sensitive values.
- Do not commit rendered files containing tokens/keys.
- Keep employer VDI portal hosts, private installer URLs, and private
  1Password item names in local config or 1Password only.
- macOS NFS AppleDouble cleanup paths intentionally live in runtime local
  config at `~/.config/nfs-dot-clean/paths`, not chezmoi template data.
