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

## Platform Behavior

Based on `.chezmoiignore` rules:

- macOS: synced
- Debian WSL2: synced
- Ubuntu WSL2: ignored
- Generic Linux (non-WSL2): ignored

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

## OpenCode in Devcontainers

For the `homelab-IaC` template, OpenCode is configured for browser-based
Plannotator plan review from inside the container.

- `PLANNOTATOR_REMOTE=1`
- `PLANNOTATOR_PORT=9999`
- `forwardPorts: [9999]`

If the browser does not open automatically when `submit_plan` runs, open:

- `http://localhost:9999`

## WSL Overlay Publishing

WSL-specific overlay publishing is handled by:

- `.chezmoiscripts/run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl`

This keeps WSL-friendly devcontainer overlays available for local workflows.
