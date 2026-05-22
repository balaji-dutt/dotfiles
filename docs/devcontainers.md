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
- `dotfiles/configs/**`
- selected `dotfiles/dot_local/bin/**` wrappers
- `dotfiles/dot_local/share/**` shell helper libraries
- `dotfiles/.config/**` and shell dotfiles used by the container build

Manifest-driven source mirroring for shared container-dotfiles is handled by:

- `configs/devcontainer-sync.jsonc`
- `./assets/sync-devcontainer-assets.sh`

Run the sync script after changing canonical host-side sources that are mirrored
into `private_Documents/development/container-dotfiles/dotfiles/**`.
Host bin wrappers are mirrored only when explicitly listed in the manifest;
`ai-wt` uses this selective mirror so container-specific wrappers can continue
to diverge when needed.

## homelab-IaC: package pins

Shared container-dotfiles config and generated inputs live under:

- `private_Documents/development/container-dotfiles/dotfiles/configs/**`

The `homelab-IaC` devcontainer has its own package pins under:

- `private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/configs/`

The main package-list files are:

- `npm_packages.txt` for global npm tools installed by `postCreate.sh` from
  `/tmp/host-homelab-configs/npm_packages.txt`
- `uv_tools.txt` for uv-installed Python tools
- `pipx_packages.txt`, which is deprecated and retained only as a pointer away
  from pipx

Renovate tracks exact `<npm-package>@<version>` lines in `npm_packages.txt`.
Do not add comments to that file; the installer loop treats each non-blank line
as an npm package spec.

`@ansible/ansible-mcp-server` is installed from this npm package list. During
`postCreate`, the devcontainer also installs `ansible-mcp-server-fixed`, which
resolves the package's `dist/cli.cjs` from the global npm root and runs it with
`node`. Configure MCP clients to use the fixed wrapper if the upstream
`ansible-mcp-server` entrypoint fails at startup.

Release-binary pins such as `MNEMO_VERSION` live in the template's
`containerEnv` and are consumed by the container-dotfiles installer. `mnemo` is
installed as a CLI only; MCP tools and automatic context injection are not
enabled by these dotfiles.

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

## homelab-IaC: Beads Dolt shared-server state

For `homelab-IaC`, Beads is configured to use the Dolt shared-server backend.
Runtime state is split between workspace files and a container-local named
volume:

- `/home/vscode/.beads/shared-server` is mounted on the
  `homelab-iac-beads-shared-server` Docker volume.
- `.beads/config.yaml` and `.beads/metadata.json` remain in the workspace as
  project state.
- `.beads/issues.jsonl` remains in the workspace and is the Git-friendly export
  to review and commit.

The named volume survives normal container restart, rebuild, and reopen cycles.
It does not survive deliberate Docker volume deletion. To exercise the terminal
rebuild path, use `devcontainer-launch.sh homelab-IaC rebuild`; if VS Code later
prompts for its own rebuild or reopen, treat that as a human-operator follow-up.

The devcontainer installs the external `dolt` CLI explicitly because the
`@beads/bd` npm package provides `bd`, not the separate Dolt server binary.
Migrating existing embedded-Dolt state into the shared server remains a separate
human-operated step; the bootstrap scripts only verify that `bd` and `dolt` are
available.

## Runtime-Generated Files

Some files are generated at render time and should not be committed:

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

## Claude Code in Devcontainers

The `homelab-IaC` devcontainer keeps Claude runtime state under
`/home/vscode/persistent-data/claude`, including the unmanaged `~/.claude.json`
state file. Managed user-level Claude assets come from dotfiles instead:

- `dot_claude/private_settings.json` -> `~/.claude/settings.json`
- `dot_claude/AGENTS.md` -> `~/.claude/AGENTS.md`
- `dot_claude/AGENTS.md` -> `~/.claude/CLAUDE.md`
- `dot_claude/agents/**` -> `~/.claude/agents/**`

`postCreate.sh` and `postStart.sh` prefer the read-only `/tmp/host-claude`
bind mount and fall back to the mirrored container-dotfiles copy seeded by
`configs/devcontainer-sync.jsonc`. The old Claude `/todo` command and
`commit-docs.sh` helper are no longer installed.

The broad container-dotfiles installer excludes `.claude/`, `.claude.json`, and
`dot_claude/` so it does not write through lifecycle-managed Claude symlinks.

If Claude or an older container run created one of these managed paths as a
regular file or directory, startup moves it into persistent backup storage under
`/home/vscode/persistent-data/claude/unmanaged-managed-path-backups/` before
installing the managed symlink.

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

The homelab devcontainer also mounts `~/.config/unslop` read-only when that
directory exists on the host. Seed `~/.config/unslop/style-memory.json` there
from a trusted private source, or generate it locally before relying on
`unslop-file-voice` in the container. Missing profile files should not block
container startup. Portable copies of that file should use a neutral `source`
key such as `dotfiles-managed` or `generated` rather than a host-specific path.

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
- `PLANNOTATOR_PORT=9999` as the direct `opencode` fallback
- `PLANNOTATOR_PORTS_BUILD=9997,9998,9999`
- `PLANNOTATOR_PORTS_CUSTOM=10007,10008,10009`

The template currently leaves the fixed `forwardPorts` and Docker-published
`appPort` mappings commented out while `opencode-plannotator*` behavior is being
debugged in the devcontainer.

If the browser does not open automatically when `submit_plan` runs, forward the
selected port and open:

- a build-handoff pool URL: `http://localhost:9997` through
  `http://localhost:9999`
- or a stay-current custom pool URL: `http://localhost:10007` through
  `http://localhost:10009`

During this experiment, VS Code auto-forwarding or manual forwarding may be
required. Terminal-only `devcontainer-launch` sessions should not assume the
review UI is reachable through pre-published localhost ports.

Container-installed `opencode-plannotator*` wrappers are verbose by default so
terminal sessions show the selected Plannotator port before OpenCode starts.
They also pause for one second before launching the TUI; set
`OPENCODE_PLANNOTATOR_LAUNCH_DELAY_SECONDS=0` to skip that pause.

If fixed Docker-published host ports are re-enabled later, stop any other
homelab devcontainer that is already publishing the same Plannotator ports before
starting another copy.

See `docs/plannotator.md` for wrapper usage, Firefox Multi-Account Containers
setup, and manual smoke tests.

## Claude Code in Devcontainers

For the `homelab-IaC` template, Claude Code runtime state is persisted under:

- `/home/vscode/persistent-data/claude`

`postCreate.sh` and `postStart.sh` link `~/.claude` and `~/.claude.json` to
this location so browser-login/auth state survives container rebuild/recreate
cycles.

Managed Claude settings and command files are still refreshed from host dotfiles
under `/tmp/host-claude`; runtime-generated login state remains in persistent
container storage and should not be committed.

The broad container-dotfiles installer excludes `.claude/`, `.claude.json`, and
`dot_claude/`; Claude config ownership stays with `postCreate.sh` and
`postStart.sh`.

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

## mnemo in Devcontainers

The `mnemo` CLI is installed from its pinned GitHub release. Its local search
index is persisted under:

- `/home/vscode/persistent-data/mnemo`

`postCreate.sh` links `~/.mnemo` to that directory so the SQLite/FTS index
survives container rebuild/recreate cycles. Host AI history directories are not
mounted into the devcontainer by default, and `mnemo` MCP/auto-context setup is
intentionally left opt-in.

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

## opencode-claude-bridge Compatibility Shim

OpenCode also loads a local `opencode-claude-bridge-compat.js` plugin on the
host and in the devcontainer dotfiles. The shim patches only Anthropic Messages
requests at the final `fetch` boundary.

By default, it keeps only the last prompt-cache `cache_control` marker, preserves
upstream active-tool filtering from `opencode-claude-bridge@1.10.10`, removes
legacy broad Claude-only stub schema injections, and removes stale
`content-length` headers after rewriting request JSON. It still filters Claude
`WebSearch` when it appears because this setup exposes `websearch_cited`, not a
bridge-mapped native OpenCode `websearch` tool. It does not log prompt text,
request bodies, request headers, or authentication values.

The managed OpenCode shell profile and `opencode-plannotator*` wrappers also
default `ANTHROPIC_SYSTEM_PROMPT_PATH` to `/dev/null` before OpenCode starts.
This prevents the bridge from reusing a stale Claude Code system prompt captured
by the validator cache. Managed interactive `opencode` launches and the
Plannotator wrappers are covered; direct non-shell launches must set the same
environment variable explicitly if they bypass the managed shell/profile setup.

Useful runtime overrides:

- `OPENCODE_CLAUDE_BRIDGE_COMPAT=0` disables the shim.
- `OPENCODE_CLAUDE_BRIDGE_CACHE_CONTROL_MAX=4` keeps up to four cache markers.
- `OPENCODE_CLAUDE_BRIDGE_FILTER_STUB_TOOLS=0` disables stub tool filtering.
- `OPENCODE_CLAUDE_BRIDGE_COMPAT_DEBUG=1` writes count-only diagnostics to
  `~/.local/state/opencode/opencode-claude-bridge-compat.log`.
- Set `ANTHROPIC_SYSTEM_PROMPT_PATH` to a non-empty alternate path before launch
  to intentionally use a custom captured system prompt cache.

## opencode-quota Anthropic Compatibility Shim

OpenCode also loads `opencode-quota-anthropic-compat.js` on the host and in the
devcontainer dotfiles. The shim patches only `GET` requests to Anthropic's Claude
OAuth usage endpoint used by `@slkiser/opencode-quota`.

The quota config sets `minIntervalMs` to `600000` so normal provider refreshes
are cached for ten minutes. The shim also caches successful Anthropic usage JSON
under `~/.local/state/opencode/`, serves fresh cache for ten minutes, and serves
last-known-good data for up to five hours on endpoint 408, 429, 5xx, timeout, or
network failures. After one of those transient failures, it backs off live usage
endpoint probes for thirty minutes by default and serves the last-known-good
cache during that window. It does not store or log request headers, bearer
tokens, or credential material.

Useful runtime overrides:

- `OPENCODE_QUOTA_ANTHROPIC_COMPAT=0` disables the shim.
- `OPENCODE_QUOTA_ANTHROPIC_CACHE_TTL_MS` changes the fresh-cache TTL.
- `OPENCODE_QUOTA_ANTHROPIC_STALE_TTL_MS` can shorten, but not extend beyond,
  the five-hour stale fallback cap.
- `OPENCODE_QUOTA_ANTHROPIC_BACKOFF_TTL_MS` changes the transient-failure
  backoff TTL, capped by the stale fallback window.
- `OPENCODE_QUOTA_ANTHROPIC_COMPAT_DEBUG=1` writes count/status-only diagnostics
  to `~/.local/state/opencode/opencode-quota-anthropic-compat.log`.

## opencode-websearch-cited Compatibility Shim

OpenCode loads `opencode-websearch-cited-compat.js` from the managed global
plugin directory on the host and devcontainer dotfiles. The npm
`opencode-websearch-cited` package is intentionally not listed in the OpenCode
plugin array because its exported OpenAI and Google API-key auth hooks override
built-in OAuth login through OpenCode's `findLast()` auth-handler selection.

The local shim exposes only the `websearch_cited` tool. It reads existing
OpenCode auth data read-only from `OPENCODE_AUTH_CONTENT` or the XDG OpenCode
auth file, supports OpenAI, Google API-key, OpenRouter, and Anthropic-compatible
web search providers, and never registers provider auth methods. HTTP errors are
sanitized to provider/status details and do not include request bodies, queries,
provider response bodies, headers, or tokens.

When multiple providers define `options.websearch_cited.model`, the shim tries
them in OpenCode provider order and falls back after missing auth or sanitized
provider failures. Request aborts do not fall back.

The host plugin source is managed under `private_dot_config/opencode/plugins/`.
Devcontainer copies are generated by `./assets/sync-devcontainer-assets.sh` via
`configs/devcontainer-sync.jsonc`; do not hand-edit the mirrored plugin file.

Useful runtime overrides:

- `OPENCODE_WEBSEARCH_GOOGLE_OAUTH_CLIENT_ID` and
  `OPENCODE_WEBSEARCH_GOOGLE_OAUTH_CLIENT_SECRET` allow Google OAuth token
  refresh when Google is selected as the web search provider. They are not needed
  for OpenAI, Anthropic, OpenRouter, or Google API-key search.

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

`devcontainer-launch` starts the container through the standalone Dev Container
CLI and then execs a shell. It does not provide VS Code's automatic
port-forwarding service. The homelab template currently has fixed Plannotator
port publishing commented out, so terminal-only sessions need VS Code attach or
another explicit forwarding mechanism for the review UI.

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
