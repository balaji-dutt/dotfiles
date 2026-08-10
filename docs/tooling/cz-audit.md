<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
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
- `.chezmoiscripts/**`: template render (when needed) + Bash or PowerShell syntax checks
- `ansible/**`: syntax check (local command or container fallback)
- `configs/**`: best-effort parse checks (advisory)
- docs/repo-only files: no `chezmoi apply --dry-run`

Chezmoi scripts are classified before managed-target lookup so their source is
always rendered and syntax-checked. PowerShell hooks are parsed but never
executed. Runtime behavior and PSScriptAnalyzer are outside this audit.

## Dependency Contract

The entrypoint itself and `chezmoi` are required:

- `cz-audit.sh` requires Bash.
- `cz-audit.ps1` requires PowerShell 7.
- `chezmoi` is required for source/target lookup and template rendering.
- Git is optional. When present, it enables automatic worktree source
  detection; `CHEZMOI_SOURCE_DIR` remains the explicit override.

Validators are resolved per check. Docker is preferred over Podman when a
container fallback is needed. Neither runtime is required when the local
validator is available.

| Check | Local or host validator | Container fallback | Normal result when unavailable | Strict result when unavailable |
| :--- | :--- | :--- | :--- | :--- |
| Bash syntax | `bash` | none | fail | fail |
| PowerShell AST parse on POSIX | `pwsh`; WSL also tries host `pwsh.exe` | `local/powershell-audit:lts` | fail | fail |
| PowerShell AST parse on Windows | current PowerShell 7 process | none | fail | fail |
| Ansible syntax | `ansible-playbook` | `local/ansible-syntax:repo` | fail | fail |
| ShellCheck | `shellcheck` | `koalaman/shellcheck:stable` | `INFO:` skip | fail |
| ansible-lint | `ansible-lint` | `local/ansible-syntax:repo` | `INFO:` skip | fail |
| YAML parse | Python + PyYAML | none | `INFO:` skip | fail |
| TOML parse | Python 3.11+ + `tomllib` | none | `INFO:` skip | fail |

An always-enforced syntax/parser check fails both for invalid input and when no
validator can run. An advisory finding is logged and passes normally. Missing
advisory tooling also passes normally with `INFO:`, but becomes a failure with
`CZ_AUDIT_STRICT=1` or its per-check strict variable, such as
`CZ_AUDIT_STRICT_SHELLCHECK=1`. Show controls affect findings; they do not turn
an unavailable validator into a successful validation.

Container command failures that indicate the fallback could not start are
treated as unavailable tooling. A validator's ordinary nonzero result is still
handled as an enforced or advisory finding according to the table.

## PowerShell Parser Selection

The POSIX wrapper chooses a PowerShell parser in this order:

1. local POSIX `pwsh`;
2. on WSL only, `pwsh.exe` from `PATH` or the standard PowerShell 7 host path;
3. Docker or Podman with `local/powershell-audit:lts`.

Templates are rendered by the current, worktree-aware `chezmoi` invocation.
The rendered or raw source is then sent to `Parser.ParseInput` over stdin. No
Linux or worktree path is passed to Windows PowerShell or a container, avoiding
UNC conversion, spaces, and alternate-worktree path differences. Parser errors
include the repo-relative source path and fail the audit.

The Windows wrapper uses `Parser.ParseInput` in its current PowerShell process
and explicitly checks the returned parse-error collection. PowerShell's parser
reports errors through that collection rather than by throwing automatically.

## Container Images

If local tooling is unavailable, `cz-audit` can use containerized fallbacks where supported.

- ShellCheck uses `koalaman/shellcheck:stable`. Docker/Podman may pull this
  public image on demand; it is not a retained local derivative.
- PowerShell parsing uses `local/powershell-audit:lts`, built from
  `assets/Dockerfile.powershell-audit` and
  `mcr.microsoft.com/powershell:lts-ubuntu-22.04`.
- Ansible syntax and lint use `local/ansible-syntax:repo`, built from
  `assets/Dockerfile.ansible-syntax` with the repository root as build context.

The audit scripts inspect each local image before fallback use and build it when
absent. They emit `INFO:` before a build because it can pull a base image or
dependencies. A build failure follows the requesting check's enforcement:
PowerShell parsing and Ansible syntax fail; ansible-lint alone remains advisory
unless strict mode is enabled.

`.chezmoiscripts/run_onchange_after_ansible_syntax_image.sh.tmpl` is an eager
cache warmer on non-Windows applies. Its rendered hash changes when the Ansible
Dockerfile changes, and it builds only when Docker is available. It does not run
when `cz-audit` starts, does not support Podman, and is not required for audit
correctness because the audit now builds a missing image lazily.

Both local Dockerfiles set `image_retention=retain`. Repository cleanup tooling
uses that label to preserve the images; Docker and Podman do not prevent manual
deletion based on the label.

## Worktree Workflow

`cz-audit` invokes `chezmoi`, which uses its configured source directory by default. When editing in a git worktree whose path differs from that source dir (e.g. `worktrees/<branch>` while chezmoi is configured for the main checkout), `cz-audit` auto-detects the mismatch and points chezmoi at the worktree.

Use the normal audit command:

```sh
./assets/cz-audit.sh check <repo-relative-path>
```

```powershell
pwsh ./assets/cz-audit.ps1 check <repo-relative-path>
```

When auto-detected, an `INFO: CHEZMOI_SOURCE_DIR auto-detected: <path>` line is emitted at the start of the run, and every internal `chezmoi` invocation is prefixed with `--source <path>`. To force a different source directory, set `CHEZMOI_SOURCE_DIR` manually; explicit overrides still emit `INFO: CHEZMOI_SOURCE_DIR override: <path>`.

## Typical Workflow

```sh
./assets/cz-audit.sh check <repo-relative-path>
chezmoi doctor
```
