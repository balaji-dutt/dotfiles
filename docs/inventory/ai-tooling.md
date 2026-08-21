<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# AI Tooling Support Matrix

This matrix records the dotfiles support contract for AI tooling. **Supported**
means the repository intentionally provisions or configures that workflow; it
does not mean that the executable is already present on every host. Each cell
names the provisioning owner and calls out manual steps. **Unsupported** means
the repository deliberately does not own that platform path.

The devcontainer column covers the managed `homelab-IaC` container lifecycle,
not arbitrary third-party images. For broader managed-file inventories, see the
[Windows](windows.md), [WSL2](wsl2.md), and [macOS](macos.md) pages.

## Core tools and dependencies

| Tool | Native Windows | WSL2 | macOS | Devcontainer |
| :--- | :--- | :--- | :--- | :--- |
| Claude Code | **Supported** — `Anthropic.ClaudeCode` in `configs/winget-packages.json`; the export-only inventory requires manual installation on a clean host. | **Supported** — `claude` in `configs/mise_wsl2.toml`, installed by the WSL Ansible playbook. | **Supported** — `claude-code@latest` cask in `brewfile.txt`. | **Supported** — `postCreate.sh` installs the pinned CLI from Anthropic's signed apt repository. |
| OpenCode | **Supported** — `opencode` in `configs/choco-packages.config`; the export-only inventory requires manual installation on a clean host. | **Supported** — `opencode` in `configs/mise_wsl2.toml`, installed by the WSL Ansible playbook. | **Supported** — `anomalyco/tap/opencode` in `brewfile.txt`. | **Supported** — `opencode-ai` in the devcontainer `configs/npm_packages.txt`, installed by `postCreate.sh`. |
| Beads (`bd`) | **Supported** — `GasTownHall.Beads` in the export-only Winget inventory remains a manual install; `.chezmoidata.yaml` owns the version, `.chezmoiscripts/run_after_windows-beads-pin.ps1.tmpl` enforces its exact Gating pin, and `.chezmoiscripts/run_after_windows-beads-client.ps1.tmpl` points `bd.exe` at the WSL2 server. | **Supported** — `.chezmoidata.yaml` owns the version and the WSL Ansible playbook writes and hydrates the Beads mise fragment. | **Supported** — `.chezmoidata.yaml` and `private_dot_config/mise/conf.d/95-beads-dolt.toml.tmpl` own the pinned mise install, not Homebrew. | **Supported** — `@beads/bd` in the devcontainer npm manifest is installed by `postCreate.sh`. |
| Dolt | **Unsupported** — the native binary is deliberately absent because Windows is only a client of the WSL2-hosted server. | **Supported** — `.chezmoidata.yaml` owns the version and the WSL Ansible playbook installs the pinned release that hosts the shared server. | **Supported** — `.chezmoidata.yaml` and `private_dot_config/mise/conf.d/95-beads-dolt.toml.tmpl` own the pinned mise install, not Homebrew. | **Supported** — `postCreate.sh` installs the separately pinned release. |
| codebase-memory-mcp | **Supported** — the checksum-verifying `install_codebase-memory-mcp.ps1` chezmoi hook installs the pinned native binary and the Claude MCP hook registers it. | **Supported** — `configs/mise.toml` owns the GitHub release and the WSL Ansible playbook runs mise. | **Supported** — `configs/mise.toml` owns the GitHub release through the non-WSL package hook. | **Supported** — `CBM_VERSION` and `postCreate.sh` own the checksum-verified portable release; lifecycle wiring registers the Claude MCP server. |
| DeepWiki MCP | **Supported** — the managed OpenCode configuration and Windows Claude MCP hook register the remote HTTPS service; there is no local server executable to provision. | **Supported** — the managed OpenCode configuration and POSIX Claude MCP hook register the remote HTTPS service; there is no local server executable to provision. | **Supported** — the managed OpenCode configuration and POSIX Claude MCP hook register the remote HTTPS service; there is no local server executable to provision. | **Supported** — the container lifecycle consumes `configs/claude-mcp.json` from the host dotfiles and registers the remote HTTPS service for Claude; there is no local server executable to provision. |
| Plannotator CLI | **Supported** — the checksum-verifying `install_plannotator.ps1` chezmoi hook installs only the pinned native binary. | **Supported** — the WSL Ansible playbook installs the pinned, checksum-verified Linux release. | **Supported** — the `install_plannotator.sh` chezmoi hook installs the pinned, checksum-verified Darwin release. | **Supported** — `PLANNOTATOR_VERSION` and `postCreate.sh` own the checksum-verified Linux release. |
| Promptfoo runtime | **Unsupported** — this repository does not provision the lockfile bundle on native Windows. | **Supported** — `configs/promptfoo-runtime/` owns the lockfile bundle and the WSL Ansible playbook installs Promptfoo with the OpenCode, Claude Agent, and direct Anthropic SDKs in one package root. | **Supported** — `configs/promptfoo-runtime/` owns the lockfile bundle and the dedicated chezmoi hook hydrates it under `~/.local/share/promptfoo-runtime`; `~/bin/promptfoo` selects that runtime. | **Supported** — the pinned devcontainer npm manifest installs Promptfoo and all three provider SDKs in the same global package tree. |
| jq | **Supported** — `jqlang.jq` in the export-only Winget inventory; install it manually on a clean host. Missing jq degrades the Claude statusline and Beads guard as documented in the Windows inventory. | **Supported** — `jq` in `configs/packages.yaml`, installed through apt by the WSL Ansible playbook. | **Supported** — `jq` in `configs/mise.toml`, installed by the non-WSL package hook. | **Supported** — the container-dotfiles installer adds the apt package when it is missing. |

`bd` and Dolt are separate executables on every platform. See
[Beads and Dolt](../beads.md) for database and guarded-sync details, and
[Plannotator](../plannotator.md) for CLI and plugin behavior.

## Provisioning foundations

These rows describe which mechanisms this repository uses for the tools above;
they do not describe every package manager that can run on a platform.

| Foundation | Native Windows | WSL2 | macOS | Devcontainer |
| :--- | :--- | :--- | :--- | :--- |
| Platform package and automation layer | **Supported** — Winget and Chocolatey files record installed state, while selected checksum-verifying PowerShell hooks automate release binaries. Package exports are not imported by chezmoi. | **Supported** — the WSL Ansible playbook owns apt packages, release binaries, and mise hydration. | **Supported** — `brewfile.txt` owns Homebrew packages and chezmoi hooks hydrate non-Homebrew tools. | **Supported** — container-dotfiles `install.sh.tmpl` and lifecycle scripts own apt, npm, and release-binary installation. |
| mise | **Unsupported** as a provisioning owner — Windows uses package inventories, manual steps, and native PowerShell hooks instead. | **Supported** — Ansible links `configs/mise.toml` and `configs/mise_wsl2.toml`, then runs `mise install`. | **Supported** — Homebrew bootstraps mise, then the non-WSL package hook runs `configs/mise.toml`. | **Unsupported** as a provisioning owner — the managed lifecycle installs apt, npm, pip/uv, and release artifacts directly. |
| Node.js and npm | **Supported** — `nodejs-lts` in the export-only Chocolatey inventory is the manual runtime owner. | **Supported** — Node LTS and npm-backed tools are owned through `configs/mise.toml`. | **Supported** — Node LTS and npm-backed tools are owned through `configs/mise.toml`. | **Supported** — the image runtime and `configs/npm_packages.txt` are consumed by `postCreate.sh`. |
| Python | **Supported** — Python entries in the export-only Chocolatey inventory are manual prerequisites; `ai-wt` requires Python 3.10 or newer and does not install it. | **Supported** — `configs/mise.toml` owns the Python runtime through the WSL Ansible mise run. | **Supported** — `configs/mise.toml` owns the Python runtime through the non-WSL package hook. | **Supported** — the container image provides Python and `postCreate.sh` uses it for lifecycle tooling. |
| uv | **Unsupported** as a Windows AI-tooling owner — no native Windows provisioning path is declared for the tools in this matrix. | **Supported** — `uv` is owned by `configs/mise.toml`; Ansible can hydrate `configs/uv_tools.txt`. | **Supported** — `uv` is owned by `configs/mise.toml`; the package hook can hydrate `configs/uv_tools.txt`. | **Supported** — `postCreate.sh` bootstraps pinned uv with pip and consumes `configs/uv_tools.txt`. |
| pipx | **Unsupported** as a Windows AI-tooling owner — no native Windows provisioning path is declared. | **Supported** — `bootstrap-wsl.sh` uses direct pipx for Ansible and lastversion; steady-state `pipx:` backends in `configs/mise.toml` are hydrated by mise, while other Python CLIs use uv. | **Supported** — pipx-backed tool entries in `configs/mise.toml` are hydrated by mise. | **Unsupported** — `configs/pipx_packages.txt` is deprecated in favor of uv. |

## AI workflow helpers

| Helper | Native Windows | WSL2 | macOS | Devcontainer |
| :--- | :--- | :--- | :--- | :--- |
| `ai-wt` | **Supported** — chezmoi manages `~/.local/ai-wt.cmd` and the adjacent Python payload; Python 3.10+ is external. OpenCode sessions select build/custom Plannotator pools without native wrapper commands. | **Supported** — chezmoi manages the canonical `~/bin/ai-wt` Python script. | **Supported** — chezmoi manages the canonical `~/bin/ai-wt` Python script. | **Supported** — `configs/devcontainer-sync.jsonc` mirrors the canonical script into container-dotfiles. |
| `cc-commit` and `oc-commit` | **Supported** — chezmoi manages native PowerShell `.ps1` wrappers in `~/.local`; same-name `.cmd` stubs refuse unsafe batch forwarding. | **Supported** — chezmoi manages the POSIX wrappers in `~/bin`. | **Supported** — chezmoi manages the POSIX wrappers in `~/bin`. | **Supported** — `configs/devcontainer-sync.jsonc` mirrors both POSIX wrappers into container-dotfiles. |
| Host Claude/OpenCode plugin refresh | **Supported** — `configs/host-ai-plugin-refresh.jsonc` triggers the admitted PowerShell onchange hook; active, ambiguous, or uninspectable OpenCode clients defer cache mutation and leave the retry pending. | **Supported** — the shared sentinel triggers the POSIX onchange hook. | **Supported** — the shared sentinel triggers the POSIX onchange hook. | **Unsupported** as this host-refresh mechanism — the managed container lifecycle owns its mirrored Claude/OpenCode assets and package state separately. |
| Plannotator agent wrappers | **Unsupported** — `claude-plannotator` and `opencode-plannotator*` require Bash; native OpenCode worktree sessions select equivalent build/custom pools through `ai-wt`. | **Supported** — chezmoi manages the Bash wrappers and their host/WSL port ranges. | **Supported** — chezmoi manages the Bash wrappers and their host port ranges. | **Supported** — container-dotfiles installs the mirrored wrappers with container-specific port ranges. |
| Guarded Beads sync | **Supported** — use the repository-owned `assets/beads-sync.ps1`; do not call native `bd dolt pull` or `push`. | **Supported** — use `assets/beads-sync.sh`. | **Supported** — use `assets/beads-sync.sh`. | **Supported** — use `assets/beads-sync.sh` when operating on this dotfiles repository. |
| Agent of Empires | **Unsupported** — tmux and POSIX process dependencies prevent native operation; use WSL2 AoE or native `ai-wt`. | **Supported** — the WSL Ansible playbook installs the pinned upstream release and chezmoi manages its config. | **Supported** — the `aoe` Homebrew formula and managed config own the host setup. | **Supported** — `postCreate.sh` installs the pinned Linux release and lifecycle scripts persist its state. |

See [AI Worktree Wrapper](../automation/ai-worktrees.md),
[Plannotator](../plannotator.md), and
[Devcontainers and Container Dotfiles](../devcontainers.md) for operational
details.

## Drift-prevention check

Run the static check before approving a plan that changes AI tooling and before
claiming its implementation:

```powershell
py -3 assets/check-ai-tooling.py
```

```sh
python3 assets/check-ai-tooling.py
```

The check reads `configs/ai-tooling-support.json` and scans active MCP runtime
declarations in the managed OpenCode configurations, `configs/claude-mcp.json`,
and Claude agent frontmatter. Every local MCP command and remote MCP server name
must map to a row above, and each mapped row must have an explicit decision and
owner or reason for all four environments.

When adding or changing an AI runtime dependency:

1. Add or update its support-matrix row and name the provisioning owner or the
   reason it is unsupported in every platform cell.
2. Add its command or MCP server alias to `configs/ai-tooling-support.json`.
3. Add any new runtime-declaration location to the policy's source patterns.
4. Run the static check and `tests/test_ai_tooling_drift.py`.

The static boundary deliberately excludes prose examples, permission patterns,
package/runtime manifests, and generated-agent source definitions. Review those
manually, including host/container mirrors, and update the policy when they add
a runtime declaration or a new declaration location. Generated outputs remain
owned by their generator workflow and must not be hand-edited for this check.

This is the local/manual stage of the policy. Platform CI is deferred until the
check has proved stable.

### Native Windows preflight

Run this before plan approval or implementation claim. Check only the commands
needed by the task, but include the full baseline for AI-tooling or Beads work:

```powershell
$required = @(
  "claude", "opencode", "bd.exe", "jq", "codebase-memory-mcp",
  "plannotator", "py", "git", "chezmoi"
)
$required += "oc-commit" # Use cc-commit for a Claude Code session.
$missing = $required | Where-Object { -not (Get-Command $_ -ErrorAction SilentlyContinue) }
if ($missing) { throw "Missing required commands: $($missing -join ', ')" }

claude --version
opencode --version
bd.exe --version
jq --version
codebase-memory-mcp --version
plannotator --version
py -3 --version
git --version
chezmoi --version
py -3 assets/check-ai-tooling.py
```

For Beads work, also run `bd.exe show <issue-id>` to verify the WSL2-hosted Dolt
server is reachable. Native Windows deliberately has no Dolt executable or local
database; their absence is not a preflight failure. For MCP-dependent work, run
`claude mcp list`, `opencode mcp list`, and
`codebase-memory-mcp cli list_projects` to verify the clients and local server.

If a task-required command or service fails, stop before approval or claim.
Follow the provisioning owner named in the matrix, or move the work to an
environment whose row is supported; do not defer discovery until implementation.

## Provisioning sources

- Windows inventories: [`configs/winget-packages.json`](../../configs/winget-packages.json) and [`configs/choco-packages.config`](../../configs/choco-packages.config)
- Shared and WSL mise manifests: [`configs/mise.toml`](../../configs/mise.toml) and [`configs/mise_wsl2.toml`](../../configs/mise_wsl2.toml)
- WSL provisioning: [`bootstrap-wsl.sh`](../../bootstrap-wsl.sh), [`configs/packages.yaml`](../../configs/packages.yaml), and [`ansible/wsl-playbook.yml`](../../ansible/wsl-playbook.yml)
- macOS packages: [`brewfile.txt`](../../brewfile.txt)
- Shared Beads/Dolt pins: [`.chezmoidata.yaml`](../../.chezmoidata.yaml) and the [macOS mise fragment](../../private_dot_config/mise/conf.d/95-beads-dolt.toml.tmpl)
- Host plugin refresh: [`configs/host-ai-plugin-refresh.jsonc`](../../configs/host-ai-plugin-refresh.jsonc), the [POSIX hook](../../.chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.sh.tmpl), and the [Windows hook](../../.chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.ps1.tmpl)
- Host helper mirror manifest: [`configs/devcontainer-sync.jsonc`](../../configs/devcontainer-sync.jsonc)
- Promptfoo host runtime: [`configs/promptfoo-runtime/`](../../configs/promptfoo-runtime), the [macOS hydration hook](../../.chezmoiscripts/run_onchange_after_install_promptfoo_runtime.sh.tmpl), the [wrapper](../../bin/executable_promptfoo), and the [WSL playbook](../../ansible/wsl-playbook.yml)
- Devcontainer package manifests: [`npm_packages.txt`](../../private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/configs/npm_packages.txt), [`uv_tools.txt`](../../private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/configs/uv_tools.txt), and [`pipx_packages.txt`](../../private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/configs/pipx_packages.txt)
- Devcontainer lifecycle: [`postCreate.sh`](../../private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/postCreate.sh) and [`container-dotfiles/install.sh.tmpl`](../../private_Documents/development/container-dotfiles/dotfiles/install.sh.tmpl)
