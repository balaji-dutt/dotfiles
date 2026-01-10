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

This directory contains **repo-only helper scripts** used to validate changes in this dotfiles repository. These scripts are intentionally **not managed/applied by chezmoi** (they should be ignored via `.chezmoiignore`).

---

## Verification Tools

| Tool | Script | Inputs | Logic |
| :--- | :--- | :--- | :--- |
| **cz-audit (Unix)** | `tools/cz-audit.sh` | Any repo-relative path (relative to `chezmoi source-path`) | Classifies the file as a managed chezmoi target vs repo-only input. If managed, runs `chezmoi diff --verbose` and `chezmoi apply --dry-run --verbose` on the computed target. If repo-only, runs best-effort checks (see below). |
| **cz-audit (Windows)** | `tools/cz-audit.ps1` | Any repo-relative path (relative to `chezmoi source-path`) | Windows-native equivalent of `cz-audit`. Runs managed-target dry-run checks when applicable. For repo-only files, runs best-effort checks and uses container fallbacks where possible. |

---

## File Classification (used by cz-audit)

| Category | Path Prefix / Examples | Typical Status | What cz-audit does |
| :--- | :--- | :--- | :--- |
| **Chezmoi config / special files** | `.chezmoi.toml(.tmpl)`, `.chezmoiignore(.tmpl)`, `.chezmoiremove(.tmpl)`, `.chezmoidata.*`, `.chezmoiroot` | Not a target file | If templated, runs `chezmoi execute-template -f ...` to ensure it renders, then runs `chezmoi doctor`. Does **not** run `apply --dry-run`. |
| **Managed targets** | `dot_*`, `private_*`, `executable_*`, `*.tmpl` that map to files under `$HOME` | Managed | Computes target path from source and runs `chezmoi diff --verbose` and `chezmoi apply --dry-run --verbose` for that target only. |
| **Chezmoi scripts** | `.chezmoiscripts/**` | Repo-only inputs executed by chezmoi | If shell-based, runs `bash -n` / `shellcheck` when possible. For templated scripts (`*.tmpl`), renders first then validates. |
| **Ansible** | `ansible/**` | Repo-only WSL2 provisioning inputs | Runs `ansible-playbook --syntax-check` (local if available; else container fallback). |
| **Assets / generators** | `assets/**` | Repo-only generators | Best-effort validation depending on file type (e.g., shell syntax checks). |
| **Configs** | `configs/**` | Repo-only package lists / YAML / TOML | Best-effort YAML/TOML parse checks if tooling is available; otherwise skips. |
| **Docs** | `docs/**`, `README.md`, `CLAUDE.md`, `TODO.md`, `AGENTS.md` | Repo-only docs | No chezmoi apply/diff. |

---

## Tooling & Container Fallbacks

`cz-audit` prefers local executables. If not found on `PATH`, it attempts to run checks via Docker/Podman containers:

| Check | Local Command | Container Image (fallback) |
| :--- | :--- | :--- |
| ShellCheck | `shellcheck` | `koalaman/shellcheck:stable` |
| Ansible syntax | `ansible-playbook` | `quay.io/ansible/ansible-runner:stable` |

If neither the local command nor a container runtime (`docker`/`podman`) is available, the check is skipped and the tool prints a note.

---

## Usage

Run from the repository root.

### macOS / Linux / WSL2

```sh
./tools/cz-audit.sh classify <repo-relative-path>
./tools/cz-audit.sh dryrun-if-managed <repo-relative-path>
./tools/cz-audit.sh check <repo-relative-path>
```

### Windows (PowerShell 7)

``` powershell
pwsh ./tools/cz-audit.ps1 classify <repo-relative-path>
pwsh ./tools/cz-audit.ps1 dryrun-if-managed <repo-relative-path>
pwsh ./tools/cz-audit.ps1 check <repo-relative-path>
```

### Examples

```
./tools/cz-audit check dot_bashrc
./tools/cz-audit check .chezmoiignore
./tools/cz-audit check .chezmoi.toml.tmpl
./tools/cz-audit check ansible/site.yml
pwsh ./tools/cz-audit.ps1 check bootstrap-wsl.sh
```
