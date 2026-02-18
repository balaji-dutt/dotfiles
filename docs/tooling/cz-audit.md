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

# cz-audit

`cz-audit` validates repo changes with chezmoi-aware checks.

## Scripts

- macOS/Linux/WSL2: `./assets/cz-audit.sh`
- Windows: `./assets/cz-audit.ps1`

## Commands

```sh
./assets/cz-audit.sh classify <repo-relative-path>
./assets/cz-audit.sh dryrun-if-managed <repo-relative-path>
./assets/cz-audit.sh check <repo-relative-path>
```

```powershell
pwsh ./assets/cz-audit.ps1 classify <repo-relative-path>
pwsh ./assets/cz-audit.ps1 dryrun-if-managed <repo-relative-path>
pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
```

## Classification Model

- `chezmoi-config`: `.chezmoi*` special/config files
- `managed`: source path that maps to a managed target on this machine
- `script`: `.chezmoiscripts/**`
- `ansible`: `ansible/**`
- `assets`: `assets/**`
- `configs`: `configs/**`
- `docs`: docs files (`docs/**`, `README.md`, `TODO.md`, `AGENTS.md`, `CLAUDE.md`)
- `repo`: other repo-only paths

## What `check` Does

- Managed targets: `chezmoi diff --verbose <target>` and `chezmoi apply --dry-run --verbose <target>`
- Chezmoi special files: template render checks + `chezmoi doctor`
- `.chezmoiscripts/**`: template render (when needed) + shell syntax checks
- `ansible/**`: syntax check (local command or container fallback)
- `configs/**`: best-effort parse checks (advisory)
- docs/repo-only files: no `chezmoi apply --dry-run`

## Container Fallbacks

If local tooling is unavailable, `cz-audit` can use containerized fallbacks where supported.

- ShellCheck: `koalaman/shellcheck:stable`
- Ansible syntax: `local/ansible-syntax:repo` (repo image)

## Typical Workflow

```sh
./assets/cz-audit.sh check <repo-relative-path>
chezmoi doctor
```
