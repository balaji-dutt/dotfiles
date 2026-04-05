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
