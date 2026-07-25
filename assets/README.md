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

# assets/

This directory contains repo-only helper scripts for this repository.

Detailed audit documentation lives in `docs/tooling/cz-audit.md`. The
agent-facing worktree merge helper is documented in
`docs/agents/worktree-merge-helper.md`.

## Common Commands

Run from the repository root.

### Browser policy sync

```sh
python3 assets/sync-browser-policies.py --check
python3 assets/sync-browser-policies.py --write
```

No mode flag is equivalent to `--write`.

### macOS / Linux / WSL2

```sh
./assets/cz-audit.sh check <repo-relative-path>
```

### Windows (PowerShell 7)

```powershell
pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
```

### Examples

```sh
./assets/cz-audit.sh check dot_bashrc
./assets/cz-audit.sh check .chezmoiignore
./assets/cz-audit.sh check .chezmoi.toml.tmpl
./assets/cz-audit.sh check ansible/site.yml
pwsh ./assets/cz-audit.ps1 check bootstrap-wsl.sh
```

## Beads sync

`bd dolt pull` cannot succeed in this repo — `bd` dirties an ignored table inside
its own pull path and then fails to merge on it. Use the helper instead. It is
the only `bd` command that is replaced; every other `bd` operation is unaffected.

### macOS / Linux / WSL2

```sh
./assets/beads-sync.sh status
./assets/beads-sync.sh pull
```

### Windows (PowerShell 7)

```powershell
pwsh ./assets/beads-sync.ps1 status
pwsh ./assets/beads-sync.ps1 pull
```

Commands are `status`, `clean`, `pull`, `push`; both accept a dry-run and a
backup flag. The helper refuses to reset any table that is not listed in
`dolt_ignore`. See `docs/beads.md` for the mechanism.

## Agent Worktree Merges

Agents landing a feature worktree should use the repo-local helper instead of
generating ad hoc shell or Python snippets:

```sh
./assets/agent-wt-merge inspect --fetch --json
./assets/agent-wt-merge ff --actor opencode
```

See `docs/agents/worktree-merge-helper.md` for the full workflow and safety
rules.
