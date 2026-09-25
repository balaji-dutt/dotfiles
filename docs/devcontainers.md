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

`assets/check-automation-provenance.py` applies the same manifest include and
exclude rules to Git-tracked paths and verifies mirror bytes, executable modes,
symlink targets, and cleanup-managed stale targets. Run it after synchronization
or use the canonical `provenance` test suite. Ignored and untracked files are not
part of committed mirror provenance. See
`docs/inventory/automation-provenance.md`.

## homelab-IaC: package pins

Shared container-dotfiles config and generated inputs live under:

- `private_Documents/development/container-dotfiles/dotfiles/configs/**`

The `homelab-IaC` devcontainer has its own package pins under:

- `private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/configs/`

The main package-list files are:

- `npm_packages.txt` for global npm tools installed by `postCreate.sh` from
  `/tmp/host-homelab-configs/npm_packages.txt`
- `promptfoo-runtime/package.json` and `package-lock.json` for the local
  Promptfoo runtime and provider SDKs
- `uv_tools.txt` for uv-installed Python tools
- `pipx_packages.txt`, which is deprecated and retained only as a pointer away
  from pipx

Renovate tracks exact `<npm-package>@<version>` lines in `npm_packages.txt` and
uses its npm manager for the Promptfoo runtime manifest. Do not add comments to
the package list; the installer loop treats each non-blank line as an npm
package spec.

Promptfoo and its three provider SDKs are grouped into the dedicated
`~/.local/share/promptfoo-runtime` package root instead of being installed
globally. `postCreate.sh` copies the devcontainer-owned manifest and lockfile
from `/tmp/host-homelab-configs/promptfoo-runtime`, then runs
`npm ci --omit=optional` with npm timing output. The lockfile pins the complete
dependency graph and `npm ci` fails when the manifest and lockfile disagree.
The installer then adds only the lockfile-pinned libSQL native binding for the
container architecture. Host and devcontainer Promptfoo runtime cohorts update
independently.

The supported Promptfoo runtime consists of the CLI plus the pinned OpenCode
SDK, Claude Agent SDK, and Anthropic SDK. Browser-provider support, local
Hugging Face/ONNX execution, and other optional provider SDKs are not
preinstalled. Add one of those features only with an explicit package and
lifecycle-script review rather than removing the omission globally.

Global npm packages still install sequentially. Each package writes start,
finish, status, and elapsed-time records to `/tmp/postCreate.log`; the Promptfoo
runtime installation records the same elapsed-time fields and npm phase timing.
npm peer and deprecation warnings remain visible and are not treated as retries
by the lifecycle script.

`postCreate.sh` stores npm's content-addressed cache at
`/home/vscode/persistent-data/npm-cache` on the existing named volume. This can
reduce downloads on later container rebuilds, but it does not make the first
cold build cache-warm. To discard suspected cache corruption from inside the
container, run
`npm --cache /home/vscode/persistent-data/npm-cache cache clean --force`; the
next rebuild repopulates it.

Claude Code is the exception: it is pinned by `CLAUDE_CODE_VERSION` in
`devcontainer.json.tmpl` and installed from Anthropic's signed apt repository,
not from `npm_packages.txt`. Renovate still tracks the upstream release with an
inline `datasource=npm` comment because the npm package version matches the
Claude Code release version.

Host plugin refreshes are separate from container lifecycle updates; see
[Host AI Plugin Refresh](automation/chezmoi-scripts.md#host-ai-plugin-refresh).

`@ansible/ansible-mcp-server` is installed from `npm_packages.txt`. During
`postCreate`, the devcontainer also installs `ansible-mcp-server-fixed`, which
resolves the package's `dist/cli.cjs` from the global npm root and runs it with
`node`. Configure MCP clients to use the fixed wrapper if the upstream
`ansible-mcp-server` entrypoint fails at startup.

`CBM_VERSION` similarly pins codebase-memory-mcp, rendered from the shared
Renovate-managed `codebase_memory_mcp_version` in `.chezmoidata.yaml`.
`postCreate.sh` downloads the matching portable Linux release archive for
compatibility with the container's glibc/libstdc++ versions, verifies it against
the upstream checksum file, and installs the binary without running CBM's
native installer or client configuration hooks. It then reconciles and verifies
`auto_index=true` through the binary's process-local config CLI. `CBM_CACHE_DIR`
points to `/home/vscode/persistent-data/codebase-memory-mcp` on the existing
local named volume, so the SQLite index and `_config.db` survive rebuilds and
do not land on the workspace bind mount. Registering the binary as a Claude MCP
server is a separate step handled by the Claude lifecycle wiring below, not by
the install block.

`TF_MCP_VERSION` pins terraform-mcp-server. `postCreate.sh` selects the Linux
amd64 or arm64 archive for the container architecture, downloads it from
HashiCorp Releases, and verifies it against the upstream SHA256SUMS file before
installation. This supports WSL2 Docker Desktop and macOS OrbStack
devcontainers without a local Go toolchain or nested Docker.

## Platform Behavior

Based on the canonical `isDevcontainerHost` predicate and `.chezmoiignore`
rules:

- macOS: synced
- Debian WSL2: synced
- Ubuntu WSL2: ignored
- Generic Linux (non-WSL2): ignored

## homelab-IaC: workspace storage

On macOS, the workspace is `~/Documents/development/homelab-IaC` on local SSD.
Working-tree files and Git metadata use the workspace bind mount; the template
does not enable a separate `.git` volume.

## homelab-IaC: Terraform/OpenTofu local working data

For `homelab-IaC`, Terraform/OpenTofu working data is kept in persistent
container storage rather than the workspace bind mount.

- `TF_DATA_ROOT` is set in the devcontainer to:
  `/home/vscode/persistent-data/terraform-data`
- the `tf` shell function derives a module-specific `TF_DATA_DIR` beneath that
  root (based on the nearest `.terraform.lock.hcl`)

This isolates provider/cache/backend metadata from host-side working data and
avoids reusing a host's `.terraform` directory inside the container.

`tf` remains the supported command for switching between OpenTofu and
Terraform (`TF_CMD=tofu|opentofu|terraform`).

## homelab-IaC: environment probing

The template pins `"userEnvProbe": "none"`. Do not remove it. The default
(`loginInteractiveShell`) makes VS Code and the devcontainer CLI behind
`devcontainer-launch` run a login interactive shell in the container, capture
its environment, and pass every variable to later `docker exec` calls as
`-e KEY=VALUE`. The container `~/.zshrc` sources
`~/.config/opencode/opencode.env`, so the probe would copy provider API keys
from a 0600 file onto host process arguments, which any local user can read
with `ps`. `interactiveShell` leaks the same way.

With the probe off, interactive terminals still source `~/.zshrc` and get the
keys and the full PATH. Processes not started from an interactive shell see
only the image environment, `containerEnv`, and `remoteEnv`. That covers the VS
Code extension host, tasks, debug adapters, lifecycle hooks, and commands run
through `devcontainer-launch exec`. They keep npm globals such as `bd` and
`opencode` through the image's `/usr/local/share/nvm/current/bin`, but they
start without the provider keys, and `~/.local/bin` is not on their PATH. Start
tools that need the keys from an interactive shell instead. If a
non-interactive process needs `~/.local/bin`, add a `remoteEnv` PATH entry
rather than re-enabling the probe.

Do not put secrets in `containerEnv` or `remoteEnv` either: those values also
travel as `docker run` or `docker exec` arguments and show up in
`docker inspect`.

## homelab-IaC: Beads and Dolt

The homelab project configures Beads for a project-local Dolt server
(`dolt.mode: server`, `dolt.shared-server: false`) with database `hliac`.
Its `.beads/config.yaml` and `.beads/metadata.json` belong to the workspace,
not to container-dotfiles. The template does not mount a Beads shared-server
volume.

The devcontainer installs `bd` from the pinned `@beads/bd` npm package and the
external `dolt` binary separately. Lifecycle hooks check tool availability and
prepare the workspace `.beads` directory; they do not initialize or migrate the
database. Follow homelab-IaC's own Beads instructions for database storage,
backup, synchronization, and schema upgrades. The dotfiles `dots` database and
its host/client setup are separate.

Dolt is the source of truth. Homelab disables JSONL auto-export and ignores
`.beads/issues.jsonl` in Git; treat that file as a disposable local export, not
as database state to commit.

Workspace files survive container recreation through the bind mount; running
Dolt processes do not. Do not treat container recreation or the general
persistent-data volume as a database backup.

### Better Beads Kanban

Better Beads Kanban (`balaji-dutt.better-beads-kanban`) is installed from a
pinned GitHub release VSIX in `postCreate.sh` and retried by `postStart.sh`.
Lifecycle scripts prefer the VS Code Server CLI and log the selected executable
before installing. They also uninstall upstream `davidcforbes.beads-kanban` and
the pre-rename fork `balaji-dutt.beads-kanban-bd-fixes` on every run: all three
contribute `beadsKanban.openBoard`, and VS Code treats each extension id as a
separate install. Troubleshoot with `/tmp/postCreate.log`, `/tmp/postStart.log`,
and:

```sh
code --list-extensions --show-versions | grep beads-kanban
```

Only the version is hand-pinned. `assets/sync-beads-kanban-pin.sh --check`
verifies the checksum in all three install sites against the release; CI runs
it on every branch.

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

The sync-tooling tests run these contracts only in disposable repositories.
They force the supported-host branch with a fake platform command, use fake
`chezmoi` and `op` commands with synthetic values, and never read the real
1Password account or write to the real container-dotfiles tree. The suite
checks mirror cleanup and idempotency, sync-before-render ordering, wrapper
argument forwarding, and statusline mismatch failures.

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
- `dot_claude/skills/**` -> `~/.claude/skills/**`

`postCreate.sh` and `postStart.sh` prefer the read-only `/tmp/host-claude`
bind mount and fall back to the mirrored container-dotfiles copy seeded by
`configs/devcontainer-sync.jsonc`.

Claude's `skills/` directory contains per-skill links, including the Unslop
family and its nested supporting files. Unrelated local skills are preserved;
same-name non-symlink conflicts use the backup location below. OpenCode's
`~/.config/opencode/skills/` uses writable managed copies, as described in the
next section.

To refresh skills in an existing container, publish the updated lifecycle helper
and skill sources through the host chezmoi/overlay and container-dotfiles sync
workflow, then stop/start the container so `postStart.sh` runs. An image rebuild
is not required for this update. Fully quit and restart Claude Code and OpenCode,
then verify that each fresh session advertises and can load `unslop-commit`.
Lifecycle tests check installed files and local-content preservation; they do
not verify live client discovery.

Both hooks then call `register_claude_mcp_servers`, which registers the
user-scope Claude MCP servers declared in the host's `configs/claude-mcp.json`
(DeepWiki and codebase-memory-mcp) by running the host's
`assets/claude-mcp-apply.py` over the read-only `/tmp/host-dotfiles` mount. The
container never runs chezmoi, so this replays the same repo-only inputs the host
apply hook uses instead of duplicating the intent. It runs after
`ensure_claude_persistence_links` so `claude mcp add` writes through to
`/home/vscode/persistent-data/claude/.claude.json` and the registration survives
a rebuild; running it from `postStart.sh` as well means host config edits apply
on the next container start. A missing mount, missing `claude`/`python3`, or a
failed applier logs a `WARN:` and never fails the hook. Full schema and
behaviour: `docs/automation/claude-mcp.md`.

`postCreate.sh` installs the Claude Code CLI from Anthropic's signed apt repo at
the `CLAUDE_CODE_VERSION` pin from `devcontainer.json.tmpl`. It removes any old
global npm `@anthropic-ai/claude-code` install first so an npm shim cannot shadow
the apt-managed binary. `dot_claude/settings-base.json` sets
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

The lifecycle hooks maintain `/home/vscode/persistent-data/git/safe-dirs`,
which the container Git config includes. They preserve existing narrow trust
entries and ensure the workspace, its `.git` directory, and the literal
`/workspaces/homelab-IaC/worktrees/*` pattern are present without duplicates.
`postCreate.sh` writes the file before supporting tools attach, and
`postStart.sh` refreshes it on each start, so OpenCode can identify generated
linked worktrees as Git worktrees instead of falling back to `/`.

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
  based on `OPENCODE_PROFILES` (legacy `OPENCODE_PROFILE` remains supported for
  compatibility). An exact `defaults` stack, including the normalized `chatgpt`
  alias, leaves `OPENCODE_CONFIG_DIR` unset so OpenCode uses its native global
  and project config precedence.
- Non-default and multi-profile stacks temporarily retain the generated runtime
  directory path used for profile transforms and workspace agent overrides.
- `postStart.sh`, `postCreate.sh`, and the profile switch hook run
  `opencode-sync-workspace-overrides` to regenerate profile-specific and
  workspace agent overrides (model, prompt, and other agent fields) from
  workspace `.opencode/opencode.json|jsonc` when possible. The direct lifecycle
  calls can still create an unused defaults snapshot; native mode does not load
  or clean that directory.
- The zsh prompt hook memoizes native defaults mode separately from generated
  profile contexts. It revalidates non-default runtime profiles without
  rewriting an unchanged snapshot.
- Lifecycle scripts install that helper at
  `~/.local/bin/opencode-sync-workspace-overrides`. The profile script prefers a
  config-root compatibility copy when present, then falls back to the lifecycle
  location.

Host, container, and PowerShell environment behavior is covered by shared
contract tests. That coverage is not a claim of live Windows or container
runtime verification. Replacing non-default snapshots with sparse profile
deltas, and cleaning stale runtime directories, is a separate follow-up phase.

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

### Plannotator review UI

The template sets `PLANNOTATOR_REMOTE=1` and a direct plain-session fallback
port of `9999`. Wrapper ranges come from `.chezmoidata.yaml` under
`plannotator_ports.devcontainer`; see [Plannotator Port Ranges](plannotator.md)
for the complete host/container table and wrapper selection.

The template does not configure fixed `forwardPorts` or Docker-published
`appPort` mappings for Plannotator. Forward the port reported when review starts
and open its localhost URL. VS Code attach can provide forwarding;
terminal-only `devcontainer-launch` sessions need an explicit forwarding path.

Container-installed `opencode-plannotator*` and `claude-plannotator` wrappers
are verbose by default so terminal sessions show the configured profile and
range before the agent starts. Plannotator reports the selected port later when
review begins.

Managed OpenCode launch paths default `ANTHROPIC_SYSTEM_PROMPT_PATH` to
`/dev/null` unless a non-empty override is set. This keeps the configured
`opencode-claude-bridge` plugin from loading a validator-captured system prompt.
Direct non-shell launches that bypass those paths must set the variable
explicitly for the same behavior. See
[Wrapper commands](plannotator.md#wrapper-commands) for launch behavior.

If the validator has been used, `opencode-claude-bridge-validate --clean-artifacts`
removes its `tmp/validate-*` artifacts, which can contain sensitive request
metadata.

The container-only `opencode-plannotator*` wrappers also run
`opencode-project-deps-guard` before OpenCode. For a Git worktree with tracked
`.opencode/package.json` and `.opencode/package-lock.json`, the guard requires
the exact `@opencode-ai/plugin` version in both files to match the selected
OpenCode CLI. It then runs a serialized, script-disabled `npm ci` when the
ignored `.opencode/node_modules` state is missing or stale. Successful state is
fingerprinted under `node_modules`, so subsequent launches do not rerun npm.
The guard verifies that npm left both tracked files byte-for-byte unchanged and
blocks startup rather than allowing OpenCode to rewrite them.

The devcontainer pins `opencode-ai` to an exact stable v1 in `npm_packages.txt`,
aligned with the WSL mise pin. Renovate proposes reviewed v1 updates without a
release-age delay; see [OpenCode v1](automation/opencode-v1.md). Rebuilding after
a CLI pin update can expose project metadata that needs an intentional update.
From the owning repository, update and review that metadata separately:

```sh
plugin_version="$(opencode --version)"
npm --prefix .opencode install --package-lock-only --save-exact \
  --ignore-scripts --no-audit --no-fund \
  "@opencode-ai/plugin@${plugin_version}"
./.opencode/bin/opencode-runtime-validate.sh
git diff --check -- .opencode/package.json .opencode/package-lock.json
git diff -- .opencode/package.json .opencode/package-lock.json
```

Commit that owning-repository change after review. The guard never updates or
commits tracked project metadata. Installing a matching older OpenCode CLI is a
temporary rollback option, not the default update policy.

See `docs/plannotator.md` for wrapper usage, Firefox Multi-Account Containers
setup, and manual smoke tests.

## Agent of Empires in Devcontainers

The `homelab-IaC` template pins the release in `AOE_VERSION`. AoE metadata is
persisted under `/home/vscode/persistent-data/agent-of-empires`;
`postCreate.sh` and `postStart.sh` link `~/.config/agent-of-empires` there so
profile/session metadata survives container rebuild/recreate cycles.

If `~/.config/agent-of-empires` already exists as a real directory, startup
scripts migrate its current contents into persistent storage before replacing it
with the symlink.

Only the managed AoE `config.toml` is refreshed from host dotfiles at startup.
Runtime-managed files (for example `state.toml`, `profiles/*/sessions.json`,
`trusted_repos.toml`, and logs) are left intact.

The managed config pins `tmux.clipboard = "enabled"` and
`tmux.status_bar = "enabled"` for OSC 52 clipboard forwarding and AoE's themed
status bar. Clipboard passthrough allows programs inside the session, including
model-generated terminal output, to pass terminal escape sequences to the outer
terminal.

AoE applies the tmux options when it creates a session. After the managed config
is refreshed, quit and recreate existing AoE sessions before testing clipboard
copying or checking the status bar.

The lifecycle helper initializes a missing `state.toml` with
`has_seen_welcome = true`. It uses no-clobber creation and leaves existing state
untouched; AoE owns the file after initialization. The managed config sets
`session.default_attach_mode = "tmux"`. Restarting the container refreshes that
config without requiring an image rebuild.

The managed AoE config enables status hooks for `waiting` and `error` events.
Those hooks call `~/bin/aoe-notify`, which first tries an optional host-side
`dev-notify-bridge` endpoint at `http://host.docker.internal:6789/notify` and
then exits successfully if no bridge is reachable. Container desktop
notifications require a reachable host bridge; see
[AoE notification wiring](agents/aoe-notifications.md).

The template sets `terminal.integrated.allowChords` to `false` so AoE receives
Ctrl+K for its command palette in non-live mode. VS Code Ctrl+K chords continue
to work when the editor has focus; when the integrated terminal has focus,
chord-prefix shortcuts are sent to the terminal instead. Reopen or rebuild an
existing devcontainer if the customization has not taken effect.

AoE is installed from the upstream Linux release archive for the detected
container architecture. WSL2/amd64 containers use `aoe-linux-amd64.tar.gz`, and
OrbStack on Apple ARM still uses `aoe-linux-arm64.tar.gz` because the process is
running inside a Linux `aarch64` container, not on Darwin.

Set `AOE_INSTALL_MODE=source` only as an explicit escape hatch when debugging a
release-binary issue. Source builds isolate Rust/npm toolchains and caches to a
temporary build root and remove them after successful install, keeping long-lived
`$HOME` paths (for example `~/.cargo` and `~/.rustup`) from accumulating AoE
bootstrap residue.

Persistence keeps AoE metadata, but not live `tmux`/agent processes from a
destroyed container.

## Retired mnemo data

The devcontainer no longer installs `mnemo` or links its index into the home
directory. Data created by earlier containers remains under:

- `/home/vscode/persistent-data/mnemo`

When `postCreate.sh` encounters the former `~/.mnemo` symlink pointing to that
directory, it removes only the symlink. It does not remove a real `~/.mnemo`
directory, a differently targeted symlink, or the persistent data. Recover or
delete the retained index manually when it is no longer needed.

## opencode-quota Anthropic Compatibility Shim

OpenCode also loads `opencode-quota-anthropic-compat.js` on the host and in the
devcontainer dotfiles. The shim patches only `GET` requests to Anthropic's Claude
OAuth usage endpoint used by `@slkiser/opencode-quota`.

With `@slkiser/opencode-quota` v4.7 or later, Anthropic Max/subscription quota
uses the Anthropic OAuth credential connected through OpenCode as its preferred
source. An `ANTHROPIC_API_KEY` alone cannot query the OAuth usage endpoint.
Claude CLI, keychain, and credentials-file discovery remain fallback sources; a
separate Claude login is not required when OpenCode OAuth is present. After a
plugin registration, config, or shim change, fully quit all OpenCode processes
and restart OpenCode so it loads the new code.

Keep this shim enabled until `@slkiser/opencode-quota` can serve
last-known-good Anthropic usage data during transient failures. Upstream v4 has
bounded OAuth 429 cooldown handling and does not mutate Claude credentials, but
it still reports quota as unavailable during cooldown. The local shim remains
only for bounded stale fallback and quieter output while Anthropic's usage
endpoint returns 429s; set `OPENCODE_QUOTA_ANTHROPIC_COMPAT=0` and restart
OpenCode to run an upstream-only test before removing it.

The quota config sets `minIntervalMs` to `600000` so normal provider refreshes
are cached for ten minutes. The shim also caches successful Anthropic usage JSON
under `~/.local/state/opencode/`, associates it with a one-way fingerprint of the
bearer token on the intercepted request, serves fresh cache for ten minutes, and
serves last-known-good data for up to five hours on endpoint 408, 429, 5xx,
timeout, or network failures. After one of those transient failures, it backs
off live usage endpoint probes for thirty minutes by default and serves the
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
All container actions, including `status`, require a native Docker CLI and a
reachable daemon. `--list` and help do not contact Docker.

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
devcontainer-launch homelab-IaC status
devcontainer-launch homelab-IaC status --json
devcontainer-launch homelab-IaC exec --existing -- zsh -ic opencode
devcontainer-launch homelab-IaC exec -- zsh -ic opencode
devcontainer-launch homelab exec -- zsh -ic claude
```

The default action is `shell`, which runs `devcontainer up` and then execs the
configured login shell in the running container. Ordinary `exec` also ensures the
container is up. Rebuild actions are explicit so terminal profiles do not
recreate containers accidentally. `stop` stops the unique matching container
only when its state is `running`; other states are reported without a stop
request. `down` removes the unique matching container entirely so the next `up`
or `shell` starts fresh. Both are successful no-ops when no container exists.
All lifecycle actions refuse ambiguous or conflicting identities, including
duplicates that are stopped.

`exec --existing -- <command> [args...]` requires one unconflicted, running
container and pins execution to its full ID. It never creates, rebuilds, or
starts a container, and returns the command's exit code unchanged. A missing or
non-running container is an error; starting it requires a separate `up` action.
Arguments after `--` are passed literally to the command. The command does not
load `~/.zshrc`, so wrap agents that need provider keys or `~/.local/bin` in
`zsh -ic` (see [environment probing](#homelab-iac-environment-probing)).

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

- macOS workspace: `/Users/balaji/Documents/development/homelab-IaC`
- macOS config:
  `~/Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/.devcontainer/devcontainer.json`
- Debian WSL2 workspace: `/mnt/devdrive/homelab-IaC`
- Debian WSL2 config:
  `/mnt/devdrive/homelab-IaC/.devcontainer/personal-wsl/devcontainer.json`

`devcontainer-launch` uses native paths for execution and explicit Docker labels
for container identity. The Debian WSL2 homelab entry uses
`\\wsl.localhost\Debian\mnt\devdrive\homelab-IaC` as its
`devcontainer.local_folder` label and the native config path above as its
`devcontainer.config_file` label. This pair matches the VS Code container;
terminal sessions can reuse it without changing its labels. Different VS Code
workspace/config spellings must be checked with `status` before assuming reuse.

`devcontainer-launch` starts the container through the standalone Dev Container
CLI and then execs a shell. It does not provide VS Code's automatic
port-forwarding service. The homelab template does not publish fixed Plannotator
ports, so terminal-only sessions need VS Code attach or another explicit
forwarding mechanism for the review UI.

Per-machine overrides use the manifest `env_prefix`:

```sh
HOMELAB_IAC_WORKSPACE=/path/to/workspace devcontainer-launch homelab-IaC
HOMELAB_IAC_CONFIG=/path/to/devcontainer.json devcontainer-launch homelab-IaC
HOMELAB_IAC_SHELL='zsh -l' devcontainer-launch homelab-IaC
```

### Identity configuration and conflicts

Each launcher's platform entry may set `identity_labels`. For Debian WSL2:

```json
{
  "identity_labels": {
    "devcontainer.local_folder": "\\\\wsl.localhost\\Debian{workspace_backslashes}",
    "devcontainer.config_file": "{config}"
  }
}
```

Both keys are required when the object is present. If omitted, the native
workspace/config pair is used. Extra labels further constrain selection. Labels
must have nonempty string values without control characters; keys use letters,
digits, dots, underscores, and hyphens, starting with a letter or digit.
`devcontainer.metadata` is not an identity key. Do not put secrets in identity
labels: status intentionally displays them.

Label templates support `{home}`, `{workspace}`, `{config}`, and
`{workspace_backslashes}`. Workspace/config overrides are resolved to absolute
native paths relative to the caller's working directory before label expansion;
symlinks are not resolved. The backslashes token replaces `/` with `\` in the
workspace path. Label values themselves are not case-folded or normalized. On
macOS the manifest uses `{workspace}` and `{config}` directly.

Lookup includes stopped containers. On Debian WSL2 it also checks the native,
`\\wsl.localhost\Debian\...`, and `\\wsl$\Debian\...` workspace/config
spellings for conflicts. A container with the same workspace/config pair but
missing a configured extra label is a conflict, not permission to create another.
Alternate identities are never silently adopted or migrated.

If selection is ambiguous or conflicting, inspect the full IDs and labels with
`status --plain`. Correct the manifest if it describes the wrong identity, or
explicitly resolve unwanted containers after checking their contents. The
launcher will not choose or delete duplicates for you. Avoid simultaneous
creation from VS Code and the launcher: these checks are snapshots, not an
atomic lock shared with other tools.

### Human and machine status

`status` only inspects Docker. It does not require the Dev Container CLI or
existing workspace/config files, and never changes container state.

In a terminal, status optionally uses the mise-managed Gum to style its heading.
Full IDs, paths, labels, states, and next steps remain visible as text.
`status --plain`, redirected output, nonempty `NO_COLOR`, unset/empty/`dumb`
`TERM`, or missing/failed Gum use plain text. There are no prompts or container
pickers. `--json` never invokes Gum; `--json` and `--plain` are mutually exclusive.

`status --json` writes one JSON document to stdout with these v1 fields:

| Field | Meaning |
|---|---|
| `schema_version` | Integer `1` |
| `launcher`, `platform` | Canonical launcher name and platform key |
| `workspace_folder`, `config` | Resolved native execution paths |
| `identity_labels` | Resolved configured identity key/value pairs |
| `matches`, `conflicts` | Arrays of `{id, state, labels}`; full IDs and only relevant identity labels |
| `ambiguous` | Whether more than one exact match exists |
| `selection` | `missing`, `unique`, `ambiguous`, or `conflict` |

`ambiguous` takes precedence over `conflict`; `unique` means exactly one match
and no conflicts, not necessarily a running container. Candidate label values
may be empty or `null` when absent. Environment variables and devcontainer
metadata labels are not included. A completed inspection returns 0 even when
selection is missing, ambiguous, or conflicting; agents must inspect the fields.
Docker/inspection failures return nonzero with diagnostics on stderr and no
successful JSON report.

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
