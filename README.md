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

# Dotfiles (chezmoi)

This repository contains my cross-platform dotfiles and provisioning flows, with [chezmoi](https://www.chezmoi.io/) as the source-of-truth manager.

## Supported Platforms

- macOS
- Linux/WSL2 (Ubuntu and Debian)
- Windows (PowerShell-focused)

## Quick Start

1. Install `chezmoi`.
2. Initialize with this repo.
3. Apply dotfiles and scripts.

```sh
chezmoi init <your-repo-url-or-local-path>
chezmoi apply
```

For machine-specific values, use templates/data (`.chezmoi.toml.tmpl`, `.chezmoidata.*`) and secret stores/runtime inputs; never commit secrets.

## Repo Layout

- `dot_*`, `private_*`, `symlink_*`, `executable_*`: chezmoi source-state naming conventions.
- `.chezmoiscripts/`: run hooks (`run_once`, `run_after`, `run_onchange`) triggered by `chezmoi apply`.
- `configs/`: package/config manifests consumed by scripts and provisioning.
- `ansible/`: WSL2 provisioning playbooks/tasks.
- `assets/`: repo-only helper scripts (audit, docs commit helper, render helpers).
- `private_Documents/`, `private_Library/`, `AppData/`: OS-specific target trees.
- `.claude/`, `.opencode/`: repo-level tooling config (not host dotfiles).

## Platform Inventories

Use these curated inventories when aligning environments:

- macOS: `docs/inventory/macos.md`
- WSL2: `docs/inventory/wsl2.md`
- Windows: `docs/inventory/windows.md`

Legacy exhaustive grid (deprecated snapshot): `docs/inventory/legacy-program-dotfiles.md`

Authoritative per-machine list is still `chezmoi managed`.

## Bootstrap and Provisioning

- WSL2 bootstrap deep dive: `docs/bootstrap/wsl2.md`
- Devcontainer/container-dotfiles model: `docs/devcontainers.md`
- Configuration/manifests reference: `docs/configs.md`

## Automation

- Chezmoi hook catalog by trigger/platform: `docs/automation/chezmoi-scripts.md`
- AI worktree wrapper for Claude/OpenCode sessions: `docs/automation/ai-worktrees.md`
- Browser policy automation: `docs/automation/browser-policies.md`
- Package-manager wrappers and docs automation details: `docs/automation/package-wrappers.md`

Note: wrappers can auto-commit package-manifest updates. Review before relying on automation in shared repositories.

## Variables and Secrets

- Full variable catalog: `docs/variables.md`
- Provide secrets at runtime or via secret manager integrations (for example 1Password).
- Never store API keys, tokens, or credentials in committed files.

## Validation Workflow

After editing any repo file, run:

```sh
./assets/cz-audit.sh check <repo-relative-path>
chezmoi doctor
```

For Windows:

```powershell
pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
chezmoi doctor
```

## Discovery Commands

- List managed targets on current machine: `chezmoi managed`
- Resolve source root: `chezmoi source-path`
- Preview target differences: `chezmoi diff --verbose <target>`
- Dry-run target apply: `chezmoi apply --dry-run --verbose <target>`

## Beads setup on a new machine

```bash
pkill -9 -f dolt 2>/dev/null; sleep 1
bd init --server --non-interactive --skip-agents --skip-hooks --prefix dots

# Remove .beads/ from .git/info/exclude if bd init added it (blocks JSONL tracking).
# No-op when not present.
grep -q "Beads fork protection" .git/info/exclude && \
  sed -i.bak '/^# Beads fork protection (bd init)$/,/^\.beads\/$/d' .git/info/exclude && \
  rm -f .git/info/exclude.bak

# Verify
git check-ignore -v .beads/issues.jsonl   # should print nothing
bd list                                    # should show issues
```

The host and dev-container zsh configs source `~/.local/share/beads-helpers.zsh`.
That helper filters dolt auto-import noise and commits `.beads/issues.jsonl`
separately after successful `bd` write commands.

### Routine cross-machine sync

`.beads/issues.jsonl` is the source of truth in git. Sync via standard `git pull`/`git push`; the next `bd` command on the other machine auto-imports any new JSONL. Avoid `bd dolt pull` — use `git pull` instead.

For recovery scenarios, filesystem caveats, or full context on each step, see [homelab-IaC's Beads notes](https://gitlab.com/servers-homelab/homelab-IaC/-/blob/main/README.md#beads-setup)
