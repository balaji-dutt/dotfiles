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

# OpenCode journal

Repo-scoped, committed notes to preserve context for AI-assisted work.
Keep entries short and factual. Prefer links to files/paths over prose.

## Conventions

- Date format: YYYY-MM-DD
- Tags: decision | convention | command | gotcha | context | followup
- Prefer bullets. Avoid long narratives.

## Entries

## 2026-05-01

- decision: Added macOS LaunchAgent-backed SSH agent relay at
  `/tmp/macos-ssh-agent/ssh-agent.sock` so `devcontainer-launch` and VS Code can
  share a stable host-side socket path.
- gotcha: Do not bind the live macOS `SSH_AUTH_SOCK` socket file directly into a
  devcontainer; mount a stable relay directory instead so host socket inode churn
  does not strand the container mount.
- convention: If the upstream macOS `SSH_AUTH_SOCK` path changes mid-session,
  reload `com.user.ssh-agent-relay` (or rerun `chezmoi apply`) before rebuilding
  or reopening the devcontainer.
- decision: Added manifest-backed `devcontainer-launch` user-bin wrapper for
  terminal-launched devcontainer shells/actions; `homelab-IaC` is the first
  launcher entry in `configs/devcontainer-sync.jsonc`.
- decision: Scoped `devcontainer-launch` and WSL devcontainer overlay publishing
  to macOS + Debian WSL2; Ubuntu WSL2 remains unsupported for container-dotfiles.
- convention: Windows Terminal and iTerm devcontainer profiles are documented in
  `docs/devcontainers.md`, not managed by chezmoi.

## 2026-05-02

- decision: `devcontainer-launch` now exposes `stop` (stop matching running
  containers) and `down` (remove matching containers whether running or
  stopped)
  by resolving containers through their `devcontainer.local_folder` and
  `devcontainer.config_file` labels.
- correction: Reverted the macOS LaunchAgent-backed SSH relay for devcontainers
  after verifying that OrbStack's native `/run/host-services/ssh-auth.sock`
  works as the supported container-side forwarding path.
- convention: The macOS `homelab-IaC` devcontainer path is now OrbStack-based,
  not a generic Docker-on-macOS configuration.
- gotcha: OrbStack's mounted `/run/host-services/ssh-auth.sock` is usable by
  root in this container but not by the `vscode` user, so the devcontainer now
  bridges it to `/tmp/orbstack-ssh-agent/ssh-auth.sock` during `postStart`.
- gotcha: A host-created macOS relay socket can be healthy on the host yet still
  return `Connection refused` when bind-mounted into an OrbStack container.
- convention: Pin `SSH_AUTH_SOCK` in the macOS devcontainer config so
  `devcontainer-launch` and VS Code share the same explicit forwarded-agent path.
- decision: Prefer mise-managed npm CLI tools for non-interactive launchers;
  `npm:@anthropic-ai/claude-code` and `npm:@devcontainers/cli` live in
  `configs/mise.toml`, and `devcontainer-launch` rejects WSL `/mnt/<drive>`
  shims while falling back to `sh "$(mise where npm:@devcontainers/cli)/devcontainer"`
  when mise does not provide a usable `devcontainer` shim.
- decision: WSL2 shell wrappers and package hydration now resolve native Linux
  `mise`, `uv`, `npm`, `npx`, `bun`, and `bunx` at call time and reject
  Windows-mounted `/mnt/<drive>` shims instead of snapshotting polluted PATH
  entries.
- decision: Persisted homelab-IaC devcontainer Claude Code runtime state under
  `/home/vscode/persistent-data/claude`; `postCreate.sh` and `postStart.sh`
  now link `~/.claude` and `~/.claude.json` there while refreshing managed
  Claude settings from `/tmp/host-claude`.
- decision: Added opt-in review-loop debug logging to
  `.opencode/plugins/review-loop-marker.js` and
  `.opencode/plugins/review-loop-enforcer.js` with
  `DOTFILES_REVIEW_MARKER_DEBUG=1` and
  `DOTFILES_REVIEW_ENFORCER_DEBUG=1`.
- convention: Review-loop marker/enforcer debug logs must be written outside
  the repo under user state (`$XDG_STATE_HOME/opencode-tooling` or
  `~/.local/state/opencode-tooling`) and avoid absolute repo paths in log
  entries.
- gotcha: Devcontainer `/tmp/host-claude` is mounted read-only; do not `chmod`
  linked files there. Copy `executable_commit-docs.sh` into persisted
  `~/.claude/commit-docs.sh` with mode `0755` instead.
- decision: Replaced single fixed Plannotator port behavior with host and
  devcontainer build/custom port pools plus `opencode-plannotator` wrappers so
  Firefox origin persistence can coexist with concurrent OpenCode sessions.
- convention: AoE global `agent_command_override.opencode` uses the build
  wrapper; custom/stay-current sessions should use `aoe add --cmd-override
  opencode-plannotator-custom` or an equivalent profile/helper.
- gotcha: Firefox Multi-Account Containers localhost assignment keys include the
  `siteContainerMap@@_` prefix and omit the colon before the port, for example
  `siteContainerMap@@_localhost8999`.
- gotcha: Plannotator Agent Switching and identity are cookie-backed for
  `localhost`, so build/custom ports need separate Firefox containers, not just
  separate port assignments in one container.
- gotcha: Host AoE reads the XDG config at `~/.config/agent-of-empires/config.toml`
  on WSL/Linux; do not symlink it to an unrendered chezmoi source template.

## 2026-04-30

- gotcha: A stale `~/.config/opencode/opencode.json` with `"plugin": []`
  shadowed the real `opencode.jsonc` during runtime profile merge, causing
  `/status` to show no loaded plugins.
- decision: Updated both host and homelab devcontainer
  `opencode-sync-workspace-overrides.sh` flows to prefer JSONC as the global
  base config, remove legacy `opencode.json`, and keep merged runtime output as
  `opencode.jsonc`.
- decision: Updated homelab devcontainer `postStart.sh` to delete persisted
  legacy `opencode.json` so startup converges on JSONC-only config state.
- gotcha: Runtime merged OpenCode config under
  `~/.config/opencode/runtime/**/opencode.jsonc` resolves `{file:./...}`
  relative to runtime dir, which breaks prompt/skill file references authored
  relative to source configs.
- decision: In both OpenCode overlay sync scripts, rebase JSONC
  `{file:./...}` and `{file:../...}` references to runtime-relative paths
  while generating merged runtime config so source configs stay portable.

## 2026-04-26

- decision: Added `renovate.json5` to global `.chezmoiignore` excludes and
  `.chezmoiscripts/run_once_after_99-cleanup-wrong-apply.sh.tmpl`
  `REMOVE_ALWAYS` cleanup list to prevent/clean accidental `$HOME` apply on
  non-repo targets.
- decision: Switched devcontainer AoE install in
  `private_Documents/development/container-dotfiles/dotfiles/install.sh.tmpl`
  from prebuilt GitHub-release Linux binary to pinned source build with
  `serve` feature to avoid glibc mismatch on bookworm-based containers.
- decision: Isolated devcontainer AoE source-build toolchains/caches to
  temporary `mktemp` paths (`CARGO_HOME`, `RUSTUP_HOME`, npm cache) in
  `private_Documents/development/container-dotfiles/dotfiles/install.sh.tmpl`
  so AoE bootstrap does not leave persistent `~/.cargo` / `~/.rustup` residue.
- decision: Made dotfiles review sentinel files session-scoped in
  `.opencode/plugins/mark-needs-review-on-file-edited.js` and
  `.opencode/plugins/enforce-dotfiles-review.js`. Each plugin instance now
  tracks `lastSessionID` via `message.updated` events and writes/reads
  `.needs_dotfiles_review.<sessionID>` — preventing cross-session reviewer
  prompt injection when running multiple OpenCode sessions (e.g., via AoE).
  Unsuffixed sentinel is retained as cold-start / backward-compat fallback.
  State file is also session-scoped: `.dotfiles_review_enforcer_state.<sessionID>.json`.

## 2026-04-25

- decision: Replaced `opencode-mystatus` with `@slkiser/opencode-quota` in
  host and devcontainer OpenCode user configs.
- decision: Removed custom `/mystatus` command blocks; quota workflows now use
  plugin-native `/quota*` commands.
- decision: Added `@gotgenes/opencode-agent-identity`, `cc-safety-net`, and
  `envsitter-guard` to both host/devcontainer OpenCode plugin lists.
- decision: Enabled quota TUI plugin in host `private_dot_config/opencode/tui.json`
  and devcontainer workspace template
  `private_Documents/development/container-dotfiles/dotfiles/assets/workspace-templates/homelab-IaC/dot_opencode/tui.json`.
- decision: Added `opencode-snip` plugin to devcontainer OpenCode config
  (pinned in container); deferred host config enablement because native
  Windows `snip` provisioning remains out of scope.
- decision: Added `snip` GitHub-release version pin to
  `configs/packages.yaml` (Renovate-managed) and WSL install/upgrade flow in
  `ansible/wsl-playbook.yml` for Debian hosts.
- decision: Added devcontainer `SNIP_VERSION` pin (Renovate-managed) in
  `private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer.json.tmpl`
  and `install_snip_gh` installer logic in
  `private_Documents/development/container-dotfiles/dotfiles/install.sh.tmpl`.
- decision: Added Homebrew `snip` provisioning via
  `brew "edouard-claude/tap/snip"` in `brewfile.txt`; native Windows `snip`
  provisioning remains deferred.
- decision: Re-applied Plannotator `workflow: plan-agent` +
  `planningAgents` allowlist in host and container OpenCode user configs after
  merge conflict clobbered those entries; also re-normalized container planner
  key to `plan-GPT-xhigh`.
- decision: Kept `@slkiser/opencode-quota` for OpenCode-native sidebar and
  `/quota*` commands, but restricted `experimental.quotaToast.enabledProviders`
  to `copilot` + `openai` in host and container OpenCode user configs to
  suppress dormant Claude auth/quota noise.
- decision: Added standalone OpenUsage provisioning on host macOS via
  `brewfile.txt` and on WSL2 via pinned GitHub release
  (`configs/packages.yaml` + `ansible/wsl-playbook.yml`); deferred devcontainer
  installation and OpenUsage telemetry integration automation.
- decision: Added Agent of Empires provisioning on macOS (`brewfile.txt`),
  WSL2 (`configs/packages.yaml` + `ansible/wsl-playbook.yml`), and devcontainer
  (`dot_devcontainer/devcontainer.json.tmpl` + `dotfiles/install.sh.tmpl`), plus
  global config defaults at `dot_agent-of-empires/config.toml` (Linux via
  symlink template and explicit container copy).

## 2026-04-23

- decision: Added Renovate MVP repo config at `renovate.json5` scoped to
  `custom.regex` managers only for pinned-version updates.
- decision: Added inline `renovate:` metadata comments for scalar pins in
  `configs/packages.yaml`, homelab
  `dot_devcontainer/devcontainer.json.tmpl`, and
  `dot_devcontainer/postCreate.sh` to keep non-standard version fields
  updatable.
- decision: Added operator runbook
  `docs/automation/renovate-gitlab-runner-setup.md` with GitLab v19-based
  service-account setup, include-ref `v26.0.0` gotcha, and first manual run
  steps.

## 2026-04-20

- decision: Added manifest-driven container sync source of truth at
  `configs/devcontainer-sync.jsonc` and maintenance script
  `assets/sync-devcontainer-assets.sh` for host-to-container-dotfiles mirroring.
- decision: Refactored `assets/sync-opencode-copilot-profiles.sh` to read
  profile derivation specs from `configs/devcontainer-sync.jsonc`.
- decision: Standardized OpenCode slash-command folder naming from singular
  `private_dot_config/opencode/command/` to plural
  `private_dot_config/opencode/commands/`.
- decision: Added non-Windows reminder script
  `.chezmoiscripts/run_onchange_after_devcontainer_sync_reminder.sh.tmpl`
  to prompt rerunning `./assets/sync-devcontainer-assets.sh` when key inputs
  change.
- decision: Added repo-local direnv integration with `.envrc` +
  `assets/sync-devcontainer-all.sh` to run the three-step
  sync/profile/render workflow via one command.
- decision: Added host `private_dot_config/direnv/direnv.toml.tmpl`
  whitelist entry scoped to `~/Documents/development/dotfiles` for
  auto-loading without prompt in this repo.
- decision: Updated `dot_zshrc.tmpl` direnv initialization to use direct
  `eval "$(direnv export zsh)"` and `eval "$(direnv hook zsh)"` instead of
  `emulate zsh -c`, fixing missing `_direnv_hook` in interactive shells.

## 2026-04-19

- decision: Added warning-only chezmoi `run_onchange` reminder script
  `.chezmoiscripts/run_onchange_after_render_container_configs_reminder.sh.tmpl`
  to prompt rerunning `./assets/render-container-configs.sh` when its tracked
  template/script inputs change.
- decision: Split drift reminders by concern with dedicated OpenCode reminder
  script `.chezmoiscripts/run_onchange_after_opencode_profiles_reminder.sh.tmpl`
  for static profile overlay drift checks.
- decision: Added manual repo-maintenance script
  `assets/sync-opencode-copilot-profiles.sh` to regenerate committed host and
  container Copilot profile overlays from canonical OpenCode user config.
- convention: Treat `assets/render-container-configs.sh` rerender reminder as
  drift detection only; secret rotation in 1Password remains a manual trigger.

## 2026-04-17

- decision: Move devcontainer workspace `.opencode` managed-manifest state from
  workspace path `.opencode/.template-managed-files` to local git metadata path
  `.git/info/dotfiles-workspace-opencode-managed` in
  `private_Documents/development/container-dotfiles/dotfiles/install.sh.tmpl`.
- gotcha: Retrying manifest writes did not fix homelab devcontainer
  `postCreateCommand` failures (`Too many open files in system`) when writing
  under workspace `.opencode`; treating manifest as local bookkeeping avoids
  that failure path.

## 2026-04-16

- decision: Updated `bin/executable_vnc_monitor.sh` disconnected behavior to enforce `RESTORE_TIMEOUT` directly instead of restoring a pre-VNC saved timeout from `/tmp` state.
- gotcha: Preserve-previous-timeout semantics can keep `idleTime=0` (`Never`) after VNC sessions if the pre-connect value was already `0`; fixed-timeout policy is more predictable for this workflow.

## 2026-04-12

- decision: For `homelab-IaC`, keep workspace template `.opencode` for local-only overlay files (for example `tui.json`) and treat repo-scoped `.opencode` files as repo-owned.
- decision: Updated workspace `.opencode` cleanup to preserve files removed from template when they are tracked by the workspace git repo.
- decision: Manage template-owned local-only `.opencode` ignore entries in a marked block in workspace `.git/info/exclude` instead of relying on template-controlled `.opencode/.gitignore`.
- decision: Inject `OP_SERVICE_ACCOUNT_TOKEN` into homelab devcontainer via rendered `container_env` (1Password service account secret), pass it through `postCreate.sh`, and persist it in container shell rc exports alongside `TAVILY_API_KEY`.

## 2026-04-11

- decision: `bin/executable_vnc_monitor.sh` now persists pre-VNC screensaver `idleTime` in `/tmp/com.user.vncmonitor.idleTime` and restores from that state on disconnect/cleanup.
- gotcha: Relying on in-memory `STATE=connected` can strand `idleTime=0` when the LaunchAgent/script restarts during an active VNC session.
- gotcha: Writing `com.apple.screensaver idleTime` without `-int` can store a string value that `defaults read` accepts but Lock Screen UI may treat as `Never`.
- decision: Added startup normalization in `bin/executable_vnc_monitor.sh` to coerce numeric `idleTime` values to integer type on LaunchAgent start.

## 2026-04-07

- decision: Mount `~/.wsl-ssh-pageant` directory into `homelab-IaC` devcontainer instead of binding the socket file directly.
- gotcha: File-level bind mount of `ssh-agent.sock` can become stale after container stop/start when host recreates socket inode.
- decision: Hardened `dot_devcontainer/postStart.sh` SSH export flow to distinguish no-keys (`ssh-add -L` exit 1) from broken agent communication.
- decision: Exempt review-gate marking for `docs/**` except `docs/agents/**`, and for `assets/README.md`.
- convention: Keep review required for `README.md`, `AGENTS.md`, `dot_claude/AGENTS.md`, and `docs/agents/**`.
- context: Added local skill scaffold at `.opencode/skills/refresh-docs/SKILL.md` and wired `skills.paths` in `.opencode/opencode.jsonc`.
- gotcha: OpenCode skills require YAML frontmatter with `name` and `description`; body-only `SKILL.md` will not load.
- decision: Override built-in `build` prompt in project config to explicitly load `refresh-docs` after review PASS when docs are stale.
- decision: Switched from `agent.build.prompt` override to repo-level `instructions` file at `.opencode/instructions/post-review-docs.md` to avoid replacing built-in Build behavior.
- convention: Rely on default discovery for `.opencode/skills/*/SKILL.md`; do not add `skills.paths` unless a non-standard location is needed.
- decision: Reverted WSL/devcontainer SSH agent preference to WSL-local `npiperelay` + `socat` socket because Windows `.wsl-ssh-pageant/ssh-agent.sock` was not reliably usable from WSL (`ssh-add` connection errors).
- context: Updated homelab devcontainer to mount `/tmp/wsl2-ssh-agent` directory and added `initializeCommand` to precreate/reconnect socket before container start.
- decision: Added project command `/refresh-docs` at `.opencode/commands/refresh-docs.md` as a manual wrapper for the `refresh-docs` skill.
- convention: `/refresh-docs` argument contract is `[/mode] [targets...]`; supported modes are `auto`, `human-only`, `agent-only`, `deep`, and unrecognized/no mode defaults to `auto`.
- decision: Override existing `build` via `.opencode/opencode.jsonc` with prompt `{file:./prompts/build.md}` so post-review docs-impact assessment is a required completion phase.
- decision: Retire global `instructions` wiring for post-review docs refresh and remove `.opencode/instructions/post-review-docs.md` in favor of Build prompt enforcement.
- decision: Install Plannotator for OpenCode declaratively via pinned plugin entry (`@plannotator/opencode@0.17.1`) in both user and homelab workspace configs; avoid upstream curl/ps1 installer.
- convention: Manage OpenCode Plannotator slash-command stubs as tracked files (`~/.config/opencode/command` and workspace `.opencode/commands`) instead of installer side effects.
- decision: For homelab devcontainer, set `PLANNOTATOR_REMOTE=1`, `PLANNOTATOR_PORT=9999`, and forward port `9999` to make browser review flow reliable from container sessions.
- decision: Moved homelab devcontainer OpenCode user config source out of `dot_devcontainer/` and into container-dotfiles `private_dot_config/opencode/**`.
- convention: Treat `assets/workspace-templates/<repo>/.opencode/**` as the authoritative managed set for workspace `.opencode` sync.
- decision: Replaced `.opencode` bootstrap-only seeding (`--ignore-existing`) with managed-file sync plus manifest cleanup for removed managed files.

## 2026-04-04

- decision: Use `os_icon` in `dot_local/config/dot_p10k.zsh` with runtime OS glyph mapping (`darwin` `\U000F0633`, Ubuntu `\uEF72`, Debian `\U000F08DA`) instead of adding a new custom prompt segment.
- gotcha: `\uEF72` for Ubuntu rendered in VS Code terminal but not Windows Terminal with JetBrainsMonoNL Nerd Font; switched Ubuntu prompt icon to `\uF31B` (`nf-linux-ubuntu`) for broader compatibility.
- gotcha: On Windows, `bash.exe` may resolve to WSL launcher, so `bash -n F:\...` fails with path escaping issues.
- decision: In `assets/cz-audit.ps1`, convert Windows paths to forward-slash form for `wslpath` and fall back to `wsl bash -n <path>` when `Get-BashPath` cannot produce a POSIX path.
- decision: Scoped `:physio` espanso expansion via app-specific configs: regular Firefox by `filter_exec` only, and FirefoxPWA by `filter_exec` + `filter_title` (`YNAB.*Mozilla Firefox$`).
- convention: Keep app-specific espanso snippets in underscored match files and include them via `extra_includes` from `espanso/config/*.yml`.
- decision: Reworked `:paidcc`, `:dpaidcc`, `:spaidcc`, and `:lpaidcc` to parse CopyQ tab `AppAutomation` via a shared espanso script flow.
- convention: Keep the new receipt parser in `configs/espanso/payment-from-copyq.py` and project it to OS targets through `.chezmoitemplates/espanso/payment-from-copyq.py.tmpl`.
- gotcha: CopyQ CLI output may be blank on Windows terminals; parser now falls back to `pwsh` + `Write-Output` when direct `copyq tab <tab> read 0` returns empty.
- gotcha: Espanso on Windows may not inherit a PATH that includes CopyQ, so parser lookup must also probe standard install paths like `C:\Program Files\CopyQ\copyq.exe`.

## 2026-04-03

- decision: Standardize WSL and devcontainer SSH agent usage on Windows `wsl-ssh-pageant` socket path.
- convention: Keep `sset` and the `npiperelay` + `socat` bridge as fallback/debug recovery only.
- context: Added explicit SSH agent socket mount and `SSH_AUTH_SOCK` env in `homelab-IaC` devcontainer overlay.
- gotcha: Guard `homelab.*` lookups in cross-platform templates with `hasKey . "homelab"` to avoid macOS render failures.
- gotcha: CopyQ GitHub releases now publish direct macOS `.dmg` assets; avoid `.dmg.zip` parsing in update script.
- convention: Select CopyQ macOS release asset by architecture (`arm64` uses `-m*`, Intel uses plain macOS `.dmg`).
- decision: Devcontainer clipboard support for `homelab-IaC` now uses OSC52 shim mode via `DEVCONTAINER_CLIPBOARD_MODE=osc52`.
- convention: Keep clipboard protocol parsing in dedicated helper `dot_local/bin/executable_devcontainer-clipboard-osc52`; keep `install.sh.tmpl` orchestration-only.
- context: `install.sh.tmpl` now wires `xclip`/`xsel`/`wl-copy` symlinks to the helper in osc52 mode and removes shim links in off mode.

## 2026-02-21

- decision: Migrate Sublime Merge command palette entries to `Packages/User/Default.sublime-commands` for macOS, WSL/Linux, and Windows.
- convention: Keep legacy `Packages/custom.sublime-commands` files as commented fallback during cross-platform validation.
- followup: Removed legacy `Packages/custom.sublime-commands` files after validating `Default.sublime-commands` on macOS, WSL/Linux, and Windows.

## 2026-02-18

- decision: Split stale root README details into focused docs under `docs/` and keep root `README.md` as concise front door.
- context: Added per-platform inventory docs at `docs/inventory/{macos,wsl2,windows}.md` and preserved a deprecated snapshot at `docs/inventory/legacy-program-dotfiles.md`.
- convention: Keep `assets/README.md` lightweight and treat `docs/tooling/cz-audit.md` as the canonical audit documentation.

## 2026-02-14

- gotcha: `.opencode/plugins/dotfiles-review-gate.js` message hooks must use generic `event`; named `"message.updated"`/`"message.part.updated"` handlers are not reliable in OpenCode plugin hooks.
- decision: Gate PASS detection should rely on final meaningful line only; do not reject when `FAIL` appears elsewhere in payload text.
- gotcha: Debug path in gate plugin referenced undefined `lastNonEmptyLine`; use `lastMeaningfulLine`.

## 2026-01-17

- context: Using OpenCode with ChatGPT Business via OAuth.
- gotcha: OAuth-backed OpenCode `openai` provider exposes limited models; `gpt-4.1*` not available there.
- convention: Use `openai/gpt-5.1-codex-mini` as `small_model` for `~/.config/opencode/opencode.jsonc` when OAuth-only.
- decision: Commit `.opencode/journal.md` as repo-scoped persistent context.
