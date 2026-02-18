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

Detailed documentation lives in `docs/tooling/cz-audit.md`.

## Common Commands

Run from the repository root.

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
