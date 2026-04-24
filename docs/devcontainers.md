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

## Runtime-Generated Files

Some files are generated at render time and should not be committed:

- `~/.claude/private_settings.json`
- `~/.claude-code-router/config.json`
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
  `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/profiles/{chatgpt,copilot}/opencode.jsonc`.
- `dot_zshrc` loads `~/.config/opencode/opencode-profile.sh`, which provides
  `opencode-profile {show|chatgpt|copilot}` and exports `OPENCODE_CONFIG_DIR`
  based on `OPENCODE_PROFILE`.
- `postStart.sh`, `postCreate.sh`, and the profile switch hook run
  `opencode-sync-workspace-overrides` to regenerate Copilot-specific model
  overrides from workspace `.opencode/opencode.json|jsonc` when possible.

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

## WSL Overlay Publishing

WSL-specific overlay publishing is handled by:

- `.chezmoiscripts/run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl`

This keeps WSL-friendly devcontainer overlays available for local workflows.
