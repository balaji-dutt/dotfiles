<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
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
- AI tooling support: `docs/inventory/ai-tooling.md`

Legacy exhaustive grid (deprecated snapshot): `docs/inventory/legacy-program-dotfiles.md`

Authoritative per-machine list is still `chezmoi managed`.

## Bootstrap and Provisioning

- WSL2 bootstrap deep dive: `docs/bootstrap/wsl2.md`
- Devcontainer/container-dotfiles model: `docs/devcontainers.md`
- Configuration/manifests reference: `docs/configs.md`

## Automation

- Chezmoi hook catalog by trigger/platform: `docs/automation/chezmoi-scripts.md`
- macOS Citrix/Zoom VDI handling: `docs/automation/macos-vdi-apps.md`
- AI worktree wrapper for Claude/OpenCode sessions: `docs/automation/ai-worktrees.md`
- Browser policy automation: `docs/automation/browser-policies.md`
- Package-manager wrappers and docs automation details: `docs/automation/package-wrappers.md`

Note: wrappers can auto-commit package-manifest updates. Review before relying on automation in shared repositories.

## Decisions

- Continue investing in Agent of Empires (multiplexer fit-gap): `docs/decisions/0001-continue-agent-of-empires.md`
- Agent isolation tiers and autonomy posture (trust tier × autonomy): `docs/decisions/0002-agent-isolation-tiers.md`

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

`cz-audit` renders and syntax-checks shell and PowerShell hooks, preferring
local validators before Docker/Podman fallbacks. See the
[audit dependency contract](docs/tooling/cz-audit.md) for platform behavior,
strict-mode rules, and container image lifecycle details.

## Discovery Commands

- List managed targets on current machine: `chezmoi managed`
- Resolve source root: `chezmoi source-path`
- Preview target differences: `chezmoi diff --verbose <target>`
- Dry-run target apply: `chezmoi apply --dry-run --verbose <target>`

## Beads setup on a new machine

The tracked Beads config intentionally omits the private Dolt remote URL. On a
fresh clone, open the 1Password Secure Note `dotfiles Dolt Remote`, copy the
Beads remote URL, then create the local override before bootstrapping:

```bash
cat > .beads/config.local.yaml <<'YAML'
sync:
  remote: "git+ssh://git@example.com/owner/private-beads.git"
YAML
```

Use the real URL from 1Password in the local file. Do not commit the generated
file; `.beads/config.local.yaml` is gitignored. As a shell-only alternative,
export `BD_SYNC_REMOTE` for the current session.

```bash
pkill -9 -f dolt 2>/dev/null; sleep 1
bd init --server --non-interactive --skip-agents --skip-hooks --prefix dots

# Remove .beads/ from .git/info/exclude if bd init added it.
# No-op when not present.
grep -q "Beads fork protection" .git/info/exclude && \
  sed -i.bak '/^# Beads fork protection (bd init)$/,/^\.beads\/$/d' .git/info/exclude && \
  rm -f .git/info/exclude.bak

# Verify
bd list                                    # should show issues
```

On non-WSL2 POSIX hosts, `bd` manages a per-project runtime port. In WSL2,
`.envrc` instead exports a stable per-checkout `BEADS_DOLT_SERVER_PORT` for the
native-Windows client. `bd` records the active port in
`.beads/dolt-server.port` in both cases. If an old clone keeps trying to use
`3318`, stop its Dolt server, remove stale `.beads/dolt-server.port` /
`.beads/dolt-server.pid`, then reload direnv or the shell.

The steps above do not apply to native Windows. That machine hosts no database
of its own: `chezmoi apply` points `BEADS_DOLT_SERVER_PORT` at the Dolt server
WSL2 runs, and `bd.exe` connects to it over `127.0.0.1`. Do not run `bd init`,
`bd dolt start`, or the PowerShell sync helper there, and do not install `dolt`.
See [`docs/beads.md`](docs/beads.md) → **Windows client mode**.

The host and dev-container shells source `~/.local/share/beads-helpers.zsh`
(zsh) or `~/.local/share/beads-helpers.bash` (bash) — the bash variant lets
agent Bash-tool invocations pick up the same helper behavior. The wrappers pass
`bd create` / `bd new` arguments unchanged. After a successful recognized
mutation in the `dots` database, they request a throttled JSONL recovery
snapshot; read-only commands and direct `command bd` / `bd.exe` calls do not.

In Dolt-backed repos, the helpers do not hide Beads auto-import diagnostics or
refresh, stage, or commit `.beads/issues.jsonl`. Snapshots are stored outside
the repository on the homelab share; `beads-sync pull` and `push` also snapshot
at their sync boundaries. Set
`BD_FILTER_AUTO_IMPORT_NOISE=1` only when you explicitly want the old filtering
behavior in another repo. If Beads reports auto-importing a stale
`.beads/issues.jsonl` in this repo, quarantine or remove that file before
syncing. See [`docs/beads.md`](docs/beads.md) for locations and recovery steps.

### Routine cross-machine sync

Beads uses the Dolt-backed model here. Auto-export to `.beads/issues.jsonl` is
disabled and that file is ignored to avoid repo churn, conflicts, and leaking
git identity metadata. Sync Beads state through the locally configured Dolt
remote.

For architecture, cross-machine sync, schema migrations on `bd` upgrades, and
recovery (re-bootstrapping after a schema bump, `database exists`, stale-server
auth), see [`docs/beads.md`](docs/beads.md). For additional filesystem caveats,
see [homelab-IaC's Beads notes](https://gitlab.com/servers-homelab/homelab-IaC/-/blob/main/README.md#beads-setup).
