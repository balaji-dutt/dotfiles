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

# Devcontainers and Container Dotfiles

This repo contains a dedicated container-dotfiles subtree used to build development containers.

## Build-Time Sync Model

Source-state location:

- `private_Documents/development/container-dotfiles/**`

Target location on host:

- `~/Documents/development/container-dotfiles/**`

Typical synced files:

- `devcontainers/**/devcontainer.json.tmpl`
- `dotfiles/install.sh`
- `dotfiles/configs/*.txt`
- `dotfiles/.config/**` and shell dotfiles used by the container build

Manifest-driven source mirroring for shared container-dotfiles is handled by:

- `configs/devcontainer-sync.jsonc`
- `./assets/sync-devcontainer-assets.sh`

Run the sync script after changing canonical host-side sources that are mirrored
into `private_Documents/development/container-dotfiles/dotfiles/**`.

## Platform Behavior

Based on `.chezmoiignore` rules:

- macOS: synced
- Debian WSL2: synced
- Ubuntu WSL2: ignored
- Generic Linux (non-WSL2): ignored

## homelab-IaC: macOS Git metadata isolation

For `homelab-IaC`, macOS uses a local Docker volume for `${containerWorkspaceFolder}/.git`.

- Working-tree files still come from the shared workspace mount.
- Git metadata (`.git/objects`, refs, index, stash, local branches) lives on
  machine-local container storage.
- On first start, container bootstrap seeds the local `.git` volume from the
  shared workspace `.git` mount.

This avoids NFS-backed Git metadata write issues on macOS while keeping file
edits shared.

## homelab-IaC: Terraform/OpenTofu local working data

For `homelab-IaC`, Terraform/OpenTofu working data is intentionally kept off the
shared workspace mount.

- `TF_DATA_ROOT` is set in the devcontainer to:
  `/home/vscode/persistent-data/terraform-data`
- the `tf` shell function derives a module-specific `TF_DATA_DIR` beneath that
  root (based on the nearest `.terraform.lock.hcl`)

This keeps provider/cache/backend metadata in persistent container storage and
prevents cross-host or cross-architecture `.terraform` reuse on shared NFS
paths.

`tf` remains the supported command for switching between OpenTofu and
Terraform (`TF_CMD=tofu|opentofu|terraform`).

## Runtime-Generated Files

Some files are generated at render time and should not be committed:

- `~/.claude/private_settings.json`
- environment exports injected during container setup (for example in shell rc files)

Use:

```sh
./assets/render-container-configs.sh
```

Run the render step before rebuilding containers so runtime values are injected safely.

The current recommended order when updating container inputs is:

1. `./assets/sync-devcontainer-assets.sh`
2. `./assets/sync-opencode-copilot-profiles.sh`
3. `./assets/render-container-configs.sh`

For routine use, a managed user-bin wrapper is also available:

- `sync-devcontainer-all.sh`

This wrapper runs the same three-step workflow from the canonical dotfiles repo
path and avoids relying on repo-local `direnv` PATH injection in editor
terminals.

## OpenCode in Devcontainers

For the `homelab-IaC` template, OpenCode is configured for browser-based
Plannotator plan review from inside the container.

Configuration ownership is split intentionally:

- User-level OpenCode config in the container (`~/.config/opencode/**`) is sourced
  from container-dotfiles under:
  `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/**`
- Repo-owned OpenCode config in the workspace (`/workspaces/<repo>/.opencode/**`)
  should live in the repo itself.
- Workspace templates under
  `private_Documents/development/container-dotfiles/dotfiles/assets/workspace-templates/<repo>/.opencode/**`
  are for local-only overlay files.

OpenCode profile switching is also supported in the `homelab-IaC` devcontainer:

- Container user profiles are sourced from
  `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/profiles/**/opencode.jsonc`
  (for example `defaults`, `copilot`, `anthropic-api`, `api-fallback`).
- `dot_zshrc` loads `~/.config/opencode/opencode-profile.sh`, which provides
  `opencode-profile {show|set <profiles...>|defaults|copilot|anthropic-api|api-fallback}`
  and exports `OPENCODE_CONFIG_DIR` based on `OPENCODE_PROFILES` (legacy
  `OPENCODE_PROFILE` remains supported for compatibility).
- `postStart.sh`, `postCreate.sh`, and the profile switch hook run
  `opencode-sync-workspace-overrides` to regenerate profile-specific and
  workspace agent overrides (model, prompt, and other agent fields) from
  workspace `.opencode/opencode.json|jsonc` when possible.

Workspace `.opencode` sync is template-whitelist based:

- Any file present in the template path is treated as managed and synced into the
  workspace `.opencode`.
- Managed files are updated when templates change.
- Previously managed files removed from the template are deleted from workspace
  `.opencode` only when they are not tracked by the workspace repo.
- Files not present in the template-managed set are preserved.
- Template-managed local-only files are mirrored into a managed block in
  `.git/info/exclude` so repo `.opencode/.gitignore` can stay repo-owned.

- `PLANNOTATOR_REMOTE=1`
- `PLANNOTATOR_PORT=9999`
- `forwardPorts: [9999]`

If the browser does not open automatically when `submit_plan` runs, open:

- `http://localhost:9999`

## Claude Code in Devcontainers

For the `homelab-IaC` template, Claude Code runtime state is persisted under:

- `/home/vscode/persistent-data/claude`

`postCreate.sh` and `postStart.sh` link `~/.claude` and `~/.claude.json` to
this location so browser-login/auth state survives container rebuild/recreate
cycles.

Managed Claude settings and command files are still refreshed from host dotfiles
under `/tmp/host-claude`; runtime-generated login state remains in persistent
container storage and should not be committed.

## Agent of Empires in Devcontainers

For the `homelab-IaC` template, AoE state is persisted under:

- `/home/vscode/persistent-data/agent-of-empires`

`postCreate.sh` and `postStart.sh` link `~/.config/agent-of-empires` to this
location so profile/session metadata survives container rebuild/recreate
cycles.

If `~/.config/agent-of-empires` already exists as a real directory, startup
scripts migrate its current contents into persistent storage before replacing it
with the symlink.

Only the managed AoE `config.toml` is refreshed from host dotfiles at startup.
Runtime-managed files (for example `profiles/*/sessions.json`,
`trusted_repos.toml`, and logs) are left intact.

AoE is built from source in this bookworm-based devcontainer instead of using
upstream prebuilt Linux release binaries. This avoids glibc version mismatches
from newer upstream build environments.

This choice affects bootstrap time and container-local disk usage (toolchain and
build artifacts), but does not require changing the external base image.

AoE build toolchain/cache directories are isolated to a temporary build root and
removed after successful install. This keeps long-lived `$HOME` paths (for
example `~/.cargo` and `~/.rustup`) from accumulating AoE bootstrap residue.

Persistence keeps AoE metadata, but not live `tmux`/agent processes from a
destroyed container.

## opencode-claude-bridge Validation

The `opencode-claude-bridge` validator can be run inside the devcontainer to
compare OpenCode wire traffic against Claude Code. It requires TypeScript only
as a project-local dev dependency — no global TypeScript install is needed.

A helper command is available after the devcontainer is created:

```sh
opencode-claude-bridge-validate
```

The bridge source is cloned or updated under persistent storage on first use:

- `/home/vscode/persistent-data/src/opencode-claude-bridge`

`npm install` installs project-local dependencies including `tsc`. Validation
output streams directly to stdout/stderr.

To clean validator artifacts (which may contain sensitive request metadata):

```sh
opencode-claude-bridge-validate --clean-artifacts
```

To run validation from the host using the `devcontainer` CLI:

```sh
devcontainer exec \
  --workspace-folder "/Volumes/devdrive/homelab-IaC" \
  --config "/Users/balaji/Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/.devcontainer/devcontainer.json" \
  opencode-claude-bridge-validate
```

Do not clone the bridge or store `node_modules` inside the mounted workspace
repo. The persistent volume path keeps all bridge state separate.

## Terminal-Launched Devcontainers

A generic host launcher is managed at:

- `~/bin/devcontainer-launch`

The launcher reads devcontainer entries from `configs/devcontainer-sync.jsonc` and
is generated only on macOS and Debian WSL2. Ubuntu WSL2 and generic Linux are
intentionally unsupported.

On Debian WSL2, the launcher prepends mise shims and requires a native Linux
`devcontainer` CLI. Windows-mounted shims from `/mnt/<drive>/...` are rejected;
`npm:@devcontainers/cli` is managed through `configs/mise.toml`.

Common commands:

```sh
devcontainer-launch --list
devcontainer-launch homelab-IaC
devcontainer-launch homelab-IaC shell
devcontainer-launch homelab-IaC up
devcontainer-launch homelab-IaC rebuild
devcontainer-launch homelab-IaC rebuild-no-cache
devcontainer-launch homelab-IaC stop
devcontainer-launch homelab-IaC down
devcontainer-launch homelab-IaC exec -- opencode
devcontainer-launch homelab exec -- claude
```

The default action is `shell`, which runs `devcontainer up` and then execs the
configured login shell in the running container. Rebuild actions are explicit so
terminal profiles do not recreate containers accidentally. `stop` stops all
matching launcher containers so they can be reused later, while `down` removes
all matching launcher containers entirely so the next `up` or `shell` starts
fresh.

On macOS with OrbStack, the homelab devcontainer bind-mounts OrbStack's native
`/run/host-services/ssh-auth.sock`, but OrbStack exposes that mounted socket as
root-only inside this container. `postStart.sh` therefore launches a small
in-container relay and points `SSH_AUTH_SOCK` at `/tmp/orbstack-ssh-agent/ssh-auth.sock`
so the `vscode` user, `devcontainer-launch`, and VS Code all share the same
user-accessible agent socket.

For `homelab-IaC`, macOS support is currently OrbStack-specific. This config
does not try to support generic Docker-on-macOS runtimes with a separate SSH
agent forwarding strategy.

If SSH agent forwarding stops working on macOS, reopen or rebuild the
devcontainer to refresh the forwarded socket mount and recreate the in-container
relay. No separate host LaunchAgent or host-side `socat` relay is required.

For `homelab-IaC`, the platform defaults are:

- macOS workspace: `/Volumes/devdrive/homelab-IaC`
- macOS config:
  `~/Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/.devcontainer/devcontainer.json`
- Debian WSL2 workspace: `/mnt/devdrive/homelab-IaC`
- Debian WSL2 config:
  `/mnt/devdrive/homelab-IaC/.devcontainer/personal-wsl/devcontainer.json`

`devcontainer-launch` uses the native Debian WSL2 workspace path
`/mnt/devdrive/homelab-IaC`. In practice, VS Code Dev Containers may still
canonicalize the same repo to a UNC path such as
`\\wsl.localhost\Debian\mnt\devdrive\homelab-IaC`, which creates a separate
container identity from the terminal launcher. If you want VS Code to use the
already-running launcher container, prefer **Dev Containers: Attach to Running
Container...** instead of assuming **Reopen in Container** will reuse it.

Per-machine overrides use the manifest `env_prefix`:

```sh
HOMELAB_IAC_WORKSPACE=/path/to/workspace devcontainer-launch homelab-IaC
HOMELAB_IAC_CONFIG=/path/to/devcontainer.json devcontainer-launch homelab-IaC
HOMELAB_IAC_SHELL='zsh -l' devcontainer-launch homelab-IaC
```

### Windows Terminal profile

Add a Windows Terminal profile that launches Debian WSL2 and runs the launcher:

```jsonc
{
  "guid": "{REPLACE-WITH-A-STABLE-GUID}",
  "name": "Homelab IaC Devcontainer",
  "commandline": "wsl.exe -d Debian --cd ~ --exec bash -lc \"exec \\\"$HOME/bin/devcontainer-launch\\\" homelab-IaC\"",
  "startingDirectory": null
}
```

### iTerm profile

For iTerm, create a profile with **Command** set to **Custom Command**:

```sh
/Users/<user>/bin/devcontainer-launch homelab-IaC
```

If using an iTerm Dynamic Profile manually, use a JSON property list such as:

```json
{
  "Profiles": [
    {
      "Name": "Homelab IaC Devcontainer",
      "Guid": "REPLACE-WITH-A-STABLE-UUID",
      "Custom Command": "Yes",
      "Command": "/Users/<user>/bin/devcontainer-launch homelab-IaC"
    }
  ]
}
```

## WSL Overlay Publishing

Debian WSL2-specific overlay publishing is handled by:

- `.chezmoiscripts/run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl`

This keeps WSL-friendly devcontainer overlays available for local workflows
without publishing them on Ubuntu WSL2.
