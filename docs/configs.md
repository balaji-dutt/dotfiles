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
| `configs/promptfoo-runtime/` | exact Promptfoo/provider SDK package manifest and npm lockfile for macOS and WSL2 hosts |
| `configs/uv_tools.txt` | `uv tool` package list |
| `configs/npm_globals.txt` | raw npm global packages |
| `configs/npm_globals_linux.txt` | raw npm globals for Linux/WSL2 |
| `configs/npm_globals_linux_debian.txt` | raw npm globals for Debian WSL2 |
| `configs/bun_globals.txt` | bun global packages |
| `configs/devcontainer-sync.jsonc` | manifest for mirrored/generated container-dotfiles inputs |
| `configs/automation-provenance.json` | non-derivable provenance authorities and accepted generated divergences |
| `configs/automation-test-inventory.json` | owned automation classification and test coverage mapping |
| `configs/gitlab-pipeline-guard.json` | main-push GitLab pipeline guard policy |
| `configs/test-suites.json` | canonical test suite, command, platform, and capability registry |
| `configs/schemas/*.schema.json` | immutable JSON Schema contracts for repository policy files |
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

Tools that must stay aligned across hosts are pinned in `.chezmoidata.yaml`
instead, so there is no second version file to drift against. `beads_version`
feeds macOS mise, WSL2 ansible, and the native-Windows Winget pin;
`dolt_version` feeds only macOS and WSL2 because Windows runs in client mode.
See `docs/beads.md` for why a Beads minor skew breaks the shared Dolt schema.

On WSL2, `ansible/wsl-playbook.yml` consumes `configs/packages.yaml`,
`configs/mise.toml`, `configs/mise_wsl2.toml`, `configs/uv_tools.txt`, and the
npm/bun manifests. The WSL provisioning hook watches those files and reruns when
they change. It also copies the Promptfoo package and lock files into
`~/.local/share/promptfoo-runtime` and runs `npm ci` through mise. macOS uses the
dedicated Promptfoo onchange hook for the same lockfile-backed runtime. The
managed `~/bin/promptfoo` wrapper always executes that co-located package tree so
provider SDK resolution does not depend on isolated npm or mise installs.

## Related Top-Level Manifest

- `brewfile.txt`: Homebrew bundle manifest for macOS, including tap-scoped
  formulae such as `Pilan-AI/tap/mnemo`. Citrix Workspace is intentionally not
  listed there; see `docs/automation/macos-vdi-apps.md`.
- `.chezmoidata.yaml`: public template data, including the Renovate-managed
  `codebase_memory_mcp_version` Windows pin, the `lazygit_version` pin, and
  non-secret macOS VDI version policy under `macos_vdi`.

## Validation

The versioned policy contracts and their owning consumers are cataloged in
`docs/tooling/config-contracts.md`. JSON Schema defines file structure; each
consumer also enforces cross-file and runtime semantics.

```sh
python3 -m unittest tests.test_config_contracts
./assets/cz-audit.sh check configs/packages.yaml
./assets/cz-audit.sh check configs/mise.toml
./assets/cz-audit.sh check configs/promptfoo-runtime/package.json
./assets/cz-audit.sh check configs/promptfoo-runtime/package-lock.json
chezmoi doctor
```
