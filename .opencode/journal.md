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

## 2026-04-12

- decision: For `homelab-IaC`, keep workspace template `.opencode` for local-only overlay files (for example `tui.json`) and treat repo-scoped `.opencode` files as repo-owned.
- decision: Updated workspace `.opencode` cleanup to preserve files removed from template when they are tracked by the workspace git repo.
- decision: Manage template-owned local-only `.opencode` ignore entries in a marked block in workspace `.git/info/exclude` instead of relying on template-controlled `.opencode/.gitignore`.

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
