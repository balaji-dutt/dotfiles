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

# Config Manifests

`configs/` contains repo-managed inputs used by scripts and provisioning.

## Core Files

| File | Purpose |
| :--- | :--- |
| `configs/packages.yaml` | WSL2 package groups and external tool version pins |
| `configs/mise.toml` | mise tool/plugin declarations, including npm CLI shims |
| `configs/mise_wsl2.toml` | WSL2-specific mise configuration |
| `configs/uv_tools.txt` | `uv tool` package list |
| `configs/npm_globals.txt` | raw npm global packages |
| `configs/npm_globals_linux.txt` | raw npm globals for Linux/WSL2 |
| `configs/npm_globals_linux_debian.txt` | raw npm globals for Debian WSL2 |
| `configs/bun_globals.txt` | bun global packages |
| `configs/devcontainer-sync.jsonc` | manifest for mirrored/generated container-dotfiles inputs |
| `configs/winget-packages.json` | Windows winget package set |
| `configs/choco-packages.config` | Windows Chocolatey package set |
| `configs/browser-policies/**` | vendored Chrome/Firefox policy artifacts |
| `configs/espanso/base.yml` | shared espanso base config |

## Package Categories in `configs/packages.yaml`

- `base_apt_packages`: common apt packages for WSL2
- `ubuntu_apt_packages`: Ubuntu-specific package additions
- `versions`: externally fetched version pins (for example lazygit/lazydocker)

On WSL2, `ansible/wsl-playbook.yml` consumes `configs/packages.yaml`,
`configs/mise.toml`, `configs/mise_wsl2.toml`, `configs/uv_tools.txt`, and the
npm/bun manifests. The WSL provisioning hook watches those files and reruns when
they change.

## Related Top-Level Manifest

- `brewfile.txt`: Homebrew bundle manifest for macOS.

## Validation

```sh
./assets/cz-audit.sh check configs/packages.yaml
./assets/cz-audit.sh check configs/mise.toml
chezmoi doctor
```
