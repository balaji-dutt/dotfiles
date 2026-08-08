<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
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
AoE config is mirrored from `private_dot_config/agent-of-empires/**`; do not
hand-edit the generated container copy. Host bin wrappers are mirrored only when
explicitly listed in the manifest; `ai-wt` uses this selective mirror so
container-specific wrappers can continue to diverge when needed.

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

Claude Code is the exception: it is pinned by `CLAUDE_CODE_VERSION` in
`devcontainer.json.tmpl` and installed from Anthropic's signed apt repository,
not from `npm_packages.txt`. Renovate still tracks the upstream release with an
inline `datasource=npm` comment because the npm package version matches the
Claude Code release version.

## Host AI Plugin Refresh Signals

Host Claude Code and OpenCode plugin refreshes are tracked separately from
container package pins in `configs/host-ai-plugin-refresh.jsonc`.

- The file is a Renovate trigger/sentinel only; host runtime configs may still
  use `@latest`.
- OpenCode sentinel versions should come from the host package cache (or npm
  latest), not from devcontainer pins.
- Update the manifest when host Claude/OpenCode plugin entries change; runtime
  config edits alone do not trigger plugin refreshes.
- Keep each manifest `version` and its `// renovate:` comment on one line so
  Renovate can match it.
- Chezmoi onchange scripts refresh Claude plugins and clear only the OpenCode
  packages cache when no blocking OpenCode session is detected. Detached or
  zombie OpenCode server processes are logged and ignored.
- Restart Claude Code/OpenCode after a refresh so the new plugin code is loaded.

`@ansible/ansible-mcp-server` is installed from this npm package list. During
`postCreate`, the devcontainer also installs `ansible-mcp-server-fixed`, which
resolves the package's `dist/cli.cjs` from the global npm root and runs it with
`node`. Configure MCP clients to use the fixed wrapper if the upstream
`ansible-mcp-server` entrypoint fails at startup.

Release-binary pins such as `MNEMO_VERSION` live in the template's
`containerEnv` and are consumed by the container-dotfiles installer. `mnemo` is
installed as a CLI only; MCP tools and automatic context injection are not
enabled by these dotfiles.

`CBM_VERSION` similarly pins codebase-memory-mcp. `postCreate.sh` downloads the
matching portable Linux release archive for compatibility with the container's
glibc/libstdc++ versions, verifies it against the upstream checksum file, and
installs only the binary; it never runs CBM's native installer or its client
configuration hooks. `CBM_CACHE_DIR` points to
`/home/vscode/persistent-data/codebase-memory-mcp` on the existing local named
volume so the SQLite cache does not land on the workspace bind mount.

## Platform Behavior

Based on the canonical `isDevcontainerHost` predicate and `.chezmoiignore`
rules:

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
- `.beads/issues.jsonl` is ignored by git. Treat it as a disposable local
  export only; remove or quarantine it before Beads sync/push workflows so Dolt
  remains the source of truth.

The named volume survives normal container restart, rebuild, and reopen cycles.
It does not survive deliberate Docker volume deletion. To exercise the terminal
rebuild path, use `devcontainer-launch.sh homelab-IaC rebuild`; if VS Code later
prompts for its own rebuild or reopen, treat that as a human-operator follow-up.

The devcontainer installs the external `dolt` CLI explicitly because the
`@beads/bd` npm package provides `bd`, not the separate Dolt server binary.
Migrating existing embedded-Dolt state into the shared server remains a separate
human-operated step; the bootstrap scripts only verify that `bd` and `dolt` are
available.

Upgrading the pinned `@beads/bd` version can advance the Dolt schema version
(for example, 1.0.4 -> 1.1.0 moves `hliac` from schema v32 to v53). Because the
`hliac` database is remote-backed, `bd` will not auto-migrate it. Migrate once
from a single designated clone with `BD_ALLOW_REMOTE_MIGRATE=1 bd migrate`
followed by `bd dolt push`, then re-bootstrap any other clones. Back up first
with a Dolt branch and `bd export --all -o ~/hliac-backup.jsonl`.

The VS Code Beads Kanban fork is installed from a pinned GitHub release VSIX in
`postCreate.sh` and retried by `postStart.sh`. Lifecycle scripts prefer the VS
Code Server CLI and log the selected executable before installing. Troubleshoot
with `/tmp/postCreate.log`, `/tmp/postStart.log`, and:

```sh
code --list-extensions --show-versions | grep beads-kanban
```

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
2. `./assets/render-container-configs.sh`

For routine use, a managed user-bin wrapper is also available:

- `sync-devcontainer-all.sh`

This wrapper runs the same sync/render workflow from the canonical dotfiles repo
path by default and avoids relying on repo-local `direnv` PATH injection in
editor terminals. To sync from a feature worktree instead, set
`DOTFILES_REPO_ROOT=/path/to/dotfiles-worktree` when invoking the wrapper.

On WSL2, this workflow is intended for Debian only. Ubuntu WSL2 is the utility
instance: Dev Container reminders and the sync/render wrapper are suppressed
there. macOS keeps the full sync/render workflow.

## Claude Code in Devcontainers

The `homelab-IaC` devcontainer keeps Claude runtime state under
`/home/vscode/persistent-data/claude`, including the unmanaged `~/.claude.json`
state file. Managed user-level Claude assets come from dotfiles instead:

- `dot_claude/settings-base.json` -> `~/.claude/settings.json` (the host's
  `modify_private_settings.json` template is not mirrored: the container
  symlinks files directly and never runs chezmoi, so it needs plain JSON)
- `dot_claude/AGENTS.md` -> `~/.claude/AGENTS.md`
- `dot_claude/AGENTS.md` -> `~/.claude/CLAUDE.md`
- `dot_claude/no-ai-isms.md` -> `~/.claude/no-ai-isms.md` (`AGENTS.md` pulls
  this in with `@~/.claude/no-ai-isms.md`, so the link must exist or the
  import resolves to a missing file)
- `dot_claude/agents/**` -> `~/.claude/agents/**`

`postCreate.sh` and `postStart.sh` prefer the read-only `/tmp/host-claude`
bind mount and fall back to the mirrored container-dotfiles copy seeded by
`configs/devcontainer-sync.jsonc`. The old Claude `/todo` command and
`commit-docs.sh` helper are no longer installed.

`postCreate.sh` installs the Claude Code CLI from Anthropic's signed apt repo at
the `CLAUDE_CODE_VERSION` pin from `devcontainer.json.tmpl`. It removes any old
global npm `@anthropic-ai/claude-code` install first so an npm shim cannot shadow
the apt-managed binary. `dot_claude/private_settings.json` sets
`DISABLE_UPDATES=1` so Claude sessions do not drift away from the devcontainer
pin through background or manual updates.

The broad container-dotfiles installer excludes `.claude/`, `.claude.json`,
`dot_claude/`, and `.config/opencode/` so it does not write through
lifecycle-managed Claude paths or overwrite persistent OpenCode config.

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

`postCreate.sh` and `postStart.sh` materialize the managed user-level OpenCode
assets by resolving the raw chezmoi source (`private_dot_config/opencode`) from
the mounted host dotfiles and copying allowlisted paths such as
`opencode.jsonc`, `profiles/`, `skills/`, `agents/`, `plugins/`, `commands/`,
and `prompts/` into the persistent `~/.config/opencode` directory. These are
real, user-writable files and directories because OpenCode writes config-local
files such as `.gitignore`, package metadata, lockfiles, and `node_modules`.
Lifecycle inventory excludes that generated package state, including package
manager symlinks under `node_modules`, when it appears in the mounted source.

The lifecycle manifest at
`/home/vscode/persistent-data/opencode/lifecycle/managed-assets.tsv` records
only source-managed entries. On later starts, lifecycle scripts refresh changed
managed files, remove entries deleted from the source, and prune empty managed
directories. Untracked and OpenCode-generated files are preserved. Existing
containers are migrated by removing the old top-level read-only symlinks before
copying; unexpected type conflicts are moved into backup storage under
`/home/vscode/persistent-data/opencode/unmanaged-managed-path-backups/`. If both
`opencode.json` and `opencode.jsonc` exist, startup keeps `opencode.jsonc` and
removes the legacy JSON file.

The broad home rsync skips OpenCode user config; lifecycle scripts install the
writable managed copies on create and start, so rebuilds never write back
through the read-only host mount.

The macOS OpenUsage telemetry hook and plugin are intentionally not mirrored
into devcontainers. They are generated as host runtime artifacts, and the
OpenUsage daemon accepts hook ingestion over a host-local Unix socket. Future
multi-machine or container aggregation should run a local daemon per source and
use daemon `export` with an authenticated `openusage hub`, rather than exposing
the host daemon socket as a network service.

The homelab devcontainer also mounts `~/.config/unslop` read-only when that
directory exists on the host. Seed `~/.config/unslop/style-memory.json` there
from a trusted private source, or generate it locally before relying on
`unslop-file-voice` in the container. Missing profile files should not block
container startup. Portable copies of that file should use a neutral `source`
key such as `dotfiles-managed` or `generated` rather than a host-specific path.

OpenCode profile switching is also supported in the `homelab-IaC` devcontainer:

- Container user profiles are sourced from
  `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/profiles/**/opencode.jsonc`
  (for example `defaults`, `anthropic-api`, `api-fallback`).
- `dot_zshrc` loads `~/.config/opencode/opencode-profile.sh`, which provides
  `opencode-profile {show|set <profiles...>|defaults|anthropic-api|api-fallback}`
  and exports `OPENCODE_CONFIG_DIR` based on `OPENCODE_PROFILES` (legacy
  `OPENCODE_PROFILE` remains supported for compatibility).
- `postStart.sh`, `postCreate.sh`, and the profile switch hook run
  `opencode-sync-workspace-overrides` to regenerate profile-specific and
  workspace agent overrides (model, prompt, and other agent fields) from
  workspace `.opencode/opencode.json|jsonc` when possible.
- Lifecycle scripts install that helper at
  `~/.local/bin/opencode-sync-workspace-overrides`. The profile script prefers a
  config-root compatibility copy when present, then falls back to the lifecycle
  location.

Workspace `.opencode` sync is template-whitelist based:

- Any file present in the template path is treated as managed and synced into the
  workspace `.opencode`.
- Managed files are updated when templates change.
- Previously managed files removed from the template are deleted from workspace
  `.opencode` only when they are not tracked by the workspace repo.
- If a repo's template set becomes empty, the previous managed files and managed
  `.git/info/exclude` block are cleaned up the same way.
- Files not present in the template-managed set are preserved.
- Template-managed local-only files are mirrored into a managed block in
  `.git/info/exclude` so repo `.opencode/.gitignore` can stay repo-owned.

- `PLANNOTATOR_REMOTE=1`
- `PLANNOTATOR_PORT=9999` as the direct plain-session fallback
- `PLANNOTATOR_PORTS_BUILD=9993-9998`
- `PLANNOTATOR_PORTS_CLAUDE=10014-10019`
- `PLANNOTATOR_PORTS_CUSTOM=10004-10009`

The template intentionally avoids fixed `forwardPorts` and Docker-published
`appPort` mappings for Plannotator. A fixed published port does not cover the
native ranges used for concurrent reviews.

If the browser does not open automatically when `submit_plan` runs, forward the
port reported by Plannotator after review starts and open:

- a build-handoff range URL: `http://localhost:9993` through
  `http://localhost:9998`
- or a stay-current custom range URL: `http://localhost:10004` through
  `http://localhost:10009`
- or a Claude Code range URL: `http://localhost:10014` through
  `http://localhost:10019`

During this experiment, VS Code auto-forwarding or manual forwarding may be
required. Terminal-only `devcontainer-launch` sessions should not assume the
review UI is reachable through pre-published localhost ports.

Container-installed `opencode-plannotator*` and `claude-plannotator` wrappers
are verbose by default so terminal sessions show the configured profile and
range before the agent starts. Plannotator reports the selected port later when
review begins; the wrappers no longer pause before launching the agent.

Keep fixed Docker-published host ports disabled unless there is a specific
reason to re-test them.

See `docs/plannotator.md` for wrapper usage, Firefox Multi-Account Containers
setup, and manual smoke tests.

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

The managed AoE config enables status hooks for `waiting` and `error` events.
Those hooks call `~/bin/aoe-notify`, which first tries an optional host-side
`dev-notify-bridge` endpoint at `http://host.docker.internal:6789/notify` and
then exits successfully if no bridge is reachable. Host bridge autostart is
tracked separately in Beads issue `dots-vlk`; until then, start the bridge
manually on the Docker Desktop host when container desktop notifications are
needed.

AoE is installed from the upstream Linux release archive for the detected
container architecture. WSL2/amd64 containers use `aoe-linux-amd64.tar.gz`, and
OrbStack on Apple ARM still uses `aoe-linux-arm64.tar.gz` because the process is
running inside a Linux `aarch64` container, not on Darwin.

Earlier devcontainer builds compiled AoE from source to avoid upstream glibc
version mismatches. Upstream now publishes Linux releases from `manylinux_2_28`
builders with a glibc `2.28` floor, so this bookworm-based container can use the
release binaries and avoid the long Rust/web build during rebuilds.

Set `AOE_INSTALL_MODE=source` only as an explicit escape hatch when debugging a
release-binary issue. Source builds isolate Rust/npm toolchains and caches to a
temporary build root and remove them after successful install, keeping long-lived
`$HOME` paths (for example `~/.cargo` and `~/.rustup`) from accumulating AoE
bootstrap residue.

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

The local `opencode-claude-bridge-compat.js` plugin is retired. Host and
devcontainer OpenCode configs now use `opencode-claude-bridge@1.10.12`, which
strips Anthropic system-block `cache_control` markers upstream. The old local
request-body rewrite, keep-last-cache-marker policy, broad legacy stub-tool
filter, `WebSearch` scrubber, stale `content-length` cleanup, and related shim
runtime toggles are no longer active.

The `WebSearch` guard was not kept as a standalone fetch wrapper. It only
removed the Claude `WebSearch` schema; it did not map calls to this setup's
custom `websearch_cited` tool. With `opencode-claude-bridge@1.10.12`, the
bridge should advertise only active OpenCode tools, so a future `WebSearch`
reappearance should be fixed in bridge/tool mapping rather than with local
fetch-body scrubbing.

The managed OpenCode shell profile and `opencode-plannotator*` wrappers also
default `ANTHROPIC_SYSTEM_PROMPT_PATH` to `/dev/null` before OpenCode starts.
This prevents the bridge from reusing a stale Claude Code system prompt captured
by the validator cache. Managed interactive `opencode` launches and the
Plannotator wrappers are covered; direct non-shell launches must set the same
environment variable explicitly if they bypass the managed shell/profile setup.

Remaining runtime override:

- Set `ANTHROPIC_SYSTEM_PROMPT_PATH` to a non-empty alternate path before launch
  to intentionally use a custom captured system prompt cache.

## opencode-quota Anthropic Compatibility Shim

OpenCode also loads `opencode-quota-anthropic-compat.js` on the host and in the
devcontainer dotfiles. The shim patches only `GET` requests to Anthropic's Claude
OAuth usage endpoint used by `@slkiser/opencode-quota`.

Keep this shim enabled until `@slkiser/opencode-quota` can serve
last-known-good Anthropic usage data during transient failures. Upstream v4 has
bounded OAuth 429 cooldown handling and does not mutate Claude credentials, but
it still reports quota as unavailable during cooldown. The local shim remains
only for bounded stale fallback and quieter output while Anthropic's usage
endpoint returns 429s; set `OPENCODE_QUOTA_ANTHROPIC_COMPAT=0` and restart
OpenCode to run an upstream-only test before removing it.

The quota config sets `minIntervalMs` to `600000` so normal provider refreshes
are cached for ten minutes. The shim also caches successful Anthropic usage JSON
under `~/.local/state/opencode/`, associates it with a local one-way Claude
OAuth token fingerprint when available, serves fresh cache for ten minutes, and
serves last-known-good data for up to five hours on endpoint 408, 429, 5xx,
timeout, or network failures. After one of those transient failures, it backs off
live usage endpoint probes for thirty minutes by default and serves the
last-known-good cache during that window.

On OpenCode launch, the shim checks local Claude credentials without making an
Anthropic network request. If the credential generation changed, it removes stale
local Anthropic usage cache and only the `@slkiser/opencode-quota` Anthropic
provider-cache files (`quota-provider-state/anthropic-*.json`); other provider
caches are untouched. It does not store or log request headers, bearer tokens,
credential JSON, or raw credential material. The local runtime state stores only
the one-way token fingerprint needed for cache invalidation.

Useful runtime overrides:

- `OPENCODE_QUOTA_ANTHROPIC_COMPAT=0` disables the shim.
- `OPENCODE_QUOTA_ANTHROPIC_CACHE_TTL_MS` changes the fresh-cache TTL.
- `OPENCODE_QUOTA_ANTHROPIC_STALE_TTL_MS` can shorten, but not extend beyond,
  the five-hour stale fallback cap.
- `OPENCODE_QUOTA_ANTHROPIC_BACKOFF_TTL_MS` changes the transient-failure
  backoff TTL, capped by the stale fallback window.
- `OPENCODE_QUOTA_ANTHROPIC_AUTH_REFRESH=0` disables launch-time Claude
  credential comparison and provider-cache eviction.
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
`npm:@devcontainers/cli` is managed through `configs/mise_wsl2.toml`.

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
