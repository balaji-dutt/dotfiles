<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Config Manifests

`configs/` contains repo-managed inputs used by scripts and provisioning.

## Core Files

| File | Purpose |
| :--- | :--- |
| `configs/packages.yaml` | WSL2 package groups and WSL external tool version pins |
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
| `configs/espanso/base.yml` | shared Espanso base match config |
| `configs/espanso/*.py` | shared Espanso script helpers rendered into platform config dirs |

## Package Categories in `configs/packages.yaml`

- `base_apt_packages`: common apt packages for WSL2
- `ubuntu_apt_packages`: Ubuntu-specific package additions
- `versions`: externally fetched WSL tool version pins (for example mnemo,
  lazydocker)

Tools that must stay identical across macOS and WSL2 are pinned in
`.chezmoidata.yaml` instead, so there is no second file to drift against. That
covers `beads_version` and `dolt_version`, which the provisioning hook passes to
ansible as extra vars; see `docs/beads.md` for why a skew there breaks the
shared Dolt schema.

On WSL2, `ansible/wsl-playbook.yml` consumes `configs/packages.yaml`,
`configs/mise.toml`, `configs/mise_wsl2.toml`, `configs/uv_tools.txt`, and the
npm/bun manifests. The WSL provisioning hook watches those files and reruns when
they change.

## Related Top-Level Manifest

- `brewfile.txt`: Homebrew bundle manifest for macOS, including tap-scoped
  formulae such as `Pilan-AI/tap/mnemo`. Citrix Workspace is intentionally not
  listed there; see `docs/automation/macos-vdi-apps.md`.
- `.chezmoidata.yaml`: public template data, including the Renovate-managed
  `codebase_memory_mcp_version` Windows pin, the `lazygit_version` pin, and
  non-secret macOS VDI version policy under `macos_vdi`.

## Validation

```sh
./assets/cz-audit.sh check configs/packages.yaml
./assets/cz-audit.sh check configs/mise.toml
chezmoi doctor
```
